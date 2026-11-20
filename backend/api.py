"""
FastAPI backend for RAGChat.
Exposes the LangGraph pipeline via REST endpoints with SSE streaming.
"""

import os
import sys
import uuid
import json
import re
import asyncio
import time
from pathlib import Path
from typing import AsyncGenerator
from concurrent.futures import ThreadPoolExecutor
from queue import Queue, Empty

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Add parent to path so we can import src/
sys.path.insert(0, str(Path(__file__).parent.parent))

# First, before any module opens a database: free the disk space held by old
# index versions, so a full volume does not keep every write failing.
from src import hybrid_index
from src.agents import list_agents
hybrid_index.reclaim_space(list_agents())

from src.graph import run_rag_pipeline, stream_rag_pipeline
from src.observability import initialize_langfuse
from src import admin_store, chat_log, eval_store, library_index, sessions_store
from src.chat_log import new_usage_collector as _new_usage_collector, record_chat as _record_chat_usage
from .admin_api import admin as admin_router, worker as worker_router
from .admin_eval_api import router as admin_eval_router
from .admin_insights_api import router as admin_insights_router
from .eval_api import router as eval_router
from .feedback_api import router as feedback_router
from .auth import access_state, get_active_user, get_active_user_id, get_current_user, require_admin
from .chat_models import ChatRequest, ChatResponse

app =FastAPI(title="RAGChat API", version="1.0.0")

# ALLOWED_ORIGINS: comma-separated list of permitted frontend origins.
# Set via env var in production, e.g.:
#   ALLOWED_ORIGINS=https://your-app.vercel.app
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

initialize_langfuse()

app.include_router(admin_router)
app.include_router(worker_router)
app.include_router(admin_insights_router)
app.include_router(eval_router)
app.include_router(admin_eval_router)
app.include_router(feedback_router)
library_index.resume_interrupted()
eval_store.fail_interrupted()  # a benchmark job running when the server stopped is gone

# Size the pool based on available CPUs.  Each streaming request blocks a
# thread for the duration of the LLM call (I/O-bound), so we allow several
# times the CPU count.  min(32, …) caps memory use on very large machines.
_executor = ThreadPoolExecutor(max_workers=min(32, (os.cpu_count() or 4) * 4))


@app.get("/api/agents")
async def get_agents(user_id: str = Depends(get_active_user_id)):
    """List available knowledge-base agents with their filter schemas (choice lists
    from the same value dictionary the question filters use)."""
    from src import hybrid_index
    from src.agents import list_agents
    from src.filter_step import ui_filter_schema
    agents = []
    for agent in list_agents():
        try:
            chunk_count = hybrid_index.count(agent)
        except Exception:
            chunk_count = 0
        agents.append({
            "id": agent["id"],
            "name": agent["name"],
            "description": agent.get("description", ""),
            "filters": ui_filter_schema(agent),
            "chunk_count": chunk_count,
        })
    return {"agents": agents}


def _daily_limit(user: dict, settings: dict) -> int:
    """Questions per day for this user; 0 means unlimited."""
    return user["daily_limit"] if user["daily_limit"] is not None else settings["default_daily_limit"]


@app.get("/api/me")
async def me(payload: dict = Depends(get_current_user)):
    """Who the caller is and what they may do — drives the UI (pending screen,
    admin link, remaining questions)."""
    state = access_state(payload)
    settings = admin_store.get_settings()
    user = state["user"]
    return {
        "email": user["email"],
        "status": user["status"],
        "is_admin": state["is_admin"],
        "allowed": state["allowed"],
        "reason": state["reason"],
        "daily_limit": 0 if state["is_admin"] else _daily_limit(user, settings),
        "questions_today": admin_store.questions_today(user["sub"]),
    }


def _require_agent(agent_id: str | None) -> None:
    """Every question searches an agent's library — there is no free-form upload store."""
    from src.agents import get_agent

    if not agent_id or not re.fullmatch(r"[A-Za-z0-9_-]+", agent_id) or get_agent(agent_id) is None:
        raise HTTPException(status_code=400, detail="יש לבחור מאגר לחיפוש")


def _enforce_quota(state: dict) -> None:
    """Refuse the question when the user's daily quota or the monthly budget is used up."""
    if state["is_admin"]:
        return
    settings = admin_store.get_settings()
    limit = _daily_limit(state["user"], settings)
    if limit and admin_store.questions_today(state["user"]["sub"]) >= limit:
        chat_log.record_refusal(state["user"]["sub"], "daily_limit")
        raise HTTPException(
            status_code=429,
            detail=f"הגעת למכסת השאלות היומית ({limit}). המכסה מתחדשת מחר.",
        )
    budget = settings["monthly_budget_usd"]
    if budget and admin_store.cost_this_month() >= budget:
        chat_log.record_refusal(state["user"]["sub"], "monthly_budget")
        raise HTTPException(
            status_code=429,
            detail="המערכת הגיעה לתקרת השימוש החודשית. פנה למנהל המערכת.",
        )


@app.post("/api/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    state: dict = Depends(get_active_user),
):
    """Run the RAG pipeline and return the result."""
    _require_agent(request.agent_id)
    _enforce_quota(state)
    user_id = state["user"]["sub"]
    session_id = request.session_id or str(uuid.uuid4())
    usage, ok = _new_usage_collector(), False
    question_id, started, result, error = uuid.uuid4().hex, time.monotonic(), None, None
    try:
        result = run_rag_pipeline(
            query=request.query,
            chat_history=request.chat_history,
            session_id=session_id,
            user_id=user_id,
            agent_id=request.agent_id,
            callbacks=[usage],
            **request.filter_args(),
        )
        ok = True
        return ChatResponse(
            answer=result["answer"],
            sources=result.get("sources", []),
            citations=result.get("citations", []),
            route=result.get("route").model_dump() if result.get("route") else None,
            rewritten=result.get("rewritten").model_dump() if result.get("rewritten") else None,
            full_doc_decision=result.get("full_doc_decision").model_dump() if result.get("full_doc_decision") else None,
            plan=result.get("plan").model_dump() if result.get("plan") else None,
            question_id=question_id,
            auto_filters=result.get("auto_filters"),
        )
    except Exception as e:
        error = str(e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _record_chat_usage(user_id, usage, request.agent_id, ok, log=dict(
            question_id=question_id, session_id=session_id, query=request.query, filters=request.filters, result=result,
            latency_ms=int((time.monotonic() - started) * 1000), first_token_ms=None, error=error))


def _serialize_pydantic(obj):
    """Safely serialize a pydantic model or return as-is."""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj


def _run_stream_in_thread(
    query: str,
    chat_history: list,
    session_id: str,
    user_id: str | None,
    q: Queue,
    agent_id: str | None = None,
    filter_args: dict | None = None,
    question_id: str | None = None,
):
    """Run the sync stream_rag_pipeline in a thread and push events to a queue."""
    usage, ok = _new_usage_collector(), False
    started, first_token_ms, result, error = time.monotonic(), None, None, None
    filter_args = filter_args or {}
    filters = filter_args.get("filters")
    try:
        for event in stream_rag_pipeline(
            query=query,
            chat_history=chat_history,
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            callbacks=[usage],
            **filter_args,
        ):
            if event["type"] == "token" and first_token_ms is None:
                first_token_ms = int((time.monotonic() - started) * 1000)
            elif event["type"] == "result":
                result = event["data"]
                event = {**event, "question_id": question_id}
            q.put(event)
        ok = True
        q.put(None)  # sentinel: done
    except Exception as e:
        error = str(e)
        q.put({"type": "error", "error": str(e)})
        q.put(None)
    finally:
        if user_id:
            _record_chat_usage(user_id, usage, agent_id, ok, log=dict(
                question_id=question_id, session_id=session_id, query=query, filters=filters,
                result=result, latency_ms=int((time.monotonic() - started) * 1000),
                first_token_ms=first_token_ms, error=error))


@app.post("/api/chat/stream")
async def chat_stream(
    request: ChatRequest,
    state: dict = Depends(get_active_user),
):
    """Stream the RAG pipeline stages as SSE events, flushed in real-time."""
    _require_agent(request.agent_id)
    _enforce_quota(state)
    user_id = state["user"]["sub"]
    session_id = request.session_id or str(uuid.uuid4())
    question_id = uuid.uuid4().hex

    async def event_generator() -> AsyncGenerator[str, None]:
        q: Queue = Queue()
        loop = asyncio.get_event_loop()

        # Run the blocking pipeline in a separate thread
        loop.run_in_executor(
            _executor,
            _run_stream_in_thread,
            request.query,
            request.chat_history,
            session_id,
            user_id,
            q,
            request.agent_id,
            request.filter_args(),
            question_id,
        )

        while True:
            # Poll the queue from the async context.
            # Use a long timeout: time-to-first-token can be slow for
            # large full-document prompts sent to the LLM.
            try:
                event = await loop.run_in_executor(None, lambda: q.get(timeout=300))
            except Empty:
                error_payload = json.dumps(
                    {"error": "Pipeline timed out waiting for response"},
                    ensure_ascii=False,
                )
                yield f"event: error\ndata: {error_payload}\n\n"
                break

            if event is None:
                # Stream finished
                yield f"event: done\ndata: {{}}\n\n"
                break

            if event.get("type") == "error":
                error_payload = json.dumps({"error": event["error"]}, ensure_ascii=False)
                yield f"event: error\ndata: {error_payload}\n\n"
                break

            if event["type"] == "step":
                payload = json.dumps({
                    "node": event["node"],
                    "label": event["label"],
                    "icon": event["icon"],
                    "detail": event.get("detail", ""),
                }, ensure_ascii=False)
                yield f"event: step\ndata: {payload}\n\n"

            elif event["type"] == "token":
                token_payload = json.dumps({
                    "content": event["content"],
                }, ensure_ascii=False)
                yield f"event: token\ndata: {token_payload}\n\n"

            elif event["type"] == "result":
                data = event["data"]
                result_payload = json.dumps({
                    "answer": data["answer"],
                    "sources": data.get("sources", []),
                    "citations": data.get("citations", []),
                    "route": _serialize_pydantic(data.get("route")),
                    "rewritten": _serialize_pydantic(data.get("rewritten")),
                    "full_doc_decision": _serialize_pydantic(data.get("full_doc_decision")),
                    "plan": _serialize_pydantic(data.get("plan")),
                    "question_id": event.get("question_id"),
                    "auto_filters": data.get("auto_filters"),
                }, ensure_ascii=False)
                yield f"event: result\ndata: {result_payload}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Type": "text/event-stream; charset=utf-8",
        },
    )


# ── Chat session persistence ─────────────────────────────────────────────
# Every endpoint here resolves the owner from the verified Google ID token,
# never from the request body or query string. A caller cannot read, mutate,
# or delete sessions belonging to a different user even by guessing their
# Google sub.

class SessionUpsertRequest(BaseModel):
    id: str
    title: str
    messages: list[dict]


@app.get("/api/sessions")
async def list_user_sessions(user_id: str = Depends(get_active_user_id)):
    """List all sessions for the authenticated user."""
    return {"sessions": sessions_store.list_sessions(user_id)}


@app.get("/api/sessions/{session_id}")
async def get_user_session(
    session_id: str,
    user_id: str = Depends(get_active_user_id),
):
    """Fetch a single session with all its messages."""
    session = sessions_store.get_session(session_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.put("/api/sessions/{session_id}")
async def upsert_user_session(
    session_id: str,
    request: SessionUpsertRequest,
    user_id: str = Depends(get_active_user_id),
):
    """Create or replace a session owned by the authenticated user."""
    if session_id != request.id:
        raise HTTPException(status_code=400, detail="path id does not match body id")
    saved = sessions_store.save_session(
        user_id=user_id,
        session_id=request.id,
        title=request.title,
        messages=request.messages,
    )
    return saved


@app.delete("/api/sessions/{session_id}")
async def delete_user_session(
    session_id: str,
    user_id: str = Depends(get_active_user_id),
):
    """Delete a session owned by the authenticated user."""
    ok = sessions_store.delete_session(session_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted"}


class MatchingRequest(BaseModel):
    filters: dict | None = None
    limit: int = 0          # 0: the count only; otherwise the newest documents too


MATCHING_LIST_MAX = 300


@app.post("/api/agents/{agent_id}/matching")
def matching_documents(agent_id: str, req: MatchingRequest, _user: dict = Depends(get_active_user)):
    """How many decisions the filter panel's choices leave, and (with `limit`) which."""
    from src.agents import get_agent
    from src.filter_step import matching_documents as match

    _require_agent(agent_id)
    if req.filters is not None and len(req.filters) > 50:
        raise HTTPException(status_code=400, detail="Too many filters")
    return match(get_agent(agent_id), req.filters, max(0, min(req.limit, MATCHING_LIST_MAX)))


@app.get("/api/sources/{agent_id}/{key}")
def get_source_document(agent_id: str, key: str, _user: dict = Depends(get_active_user)):
    """A cited library document as the parser saw it, plus links to the original
    files — what the chat opens when a user clicks a citation."""
    from src.agents import get_agent
    from src.library_store import get_document

    if not re.fullmatch(r"[A-Za-z0-9_-]+", agent_id) or get_agent(agent_id) is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    doc = get_document(agent_id, key)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return {
        "record_key": doc["record_key"],
        "title": doc["title"],
        "pages": doc.get("pages"),
        "files": doc["files"],
        "parsed_pages": doc.get("parsed_pages", []),
    }


@app.get("/api/health")
async def health():
    from src.observability import is_langfuse_enabled
    return {
        "status": "ok",
        "langfuse": is_langfuse_enabled(),
    }


@app.get("/api/debug/langfuse")
async def debug_langfuse(_admin: dict = Depends(require_admin)):
    """
    Diagnostic endpoint — tests every layer of the Langfuse integration.
    Safe to call in production (read-only / single test trace).
    """
    import uuid as _uuid
    result: dict = {}

    # ── 1. Config / env vars ──────────────────────────────────────────────
    from config import LANGFUSE_ENABLED, LANGFUSE_SECRET_KEY, LANGFUSE_HOST
    from src.observability import _langfuse_initialized
    result["env_vars_set"] = LANGFUSE_ENABLED
    result["host"] = LANGFUSE_HOST
    result["initialized"] = _langfuse_initialized
    result["secret_key_prefix"] = (LANGFUSE_SECRET_KEY or "")[:8] + "…" if LANGFUSE_SECRET_KEY else None

    if not LANGFUSE_ENABLED or not _langfuse_initialized:
        result["verdict"] = "SKIP – Langfuse not enabled/initialized"
        return result

    # ── 2. SDK flush (verifies client is alive) ───────────────────────────
    try:
        from langfuse import Langfuse
        client = Langfuse()
        client.flush()
        result["sdk_flush"] = "OK"
    except Exception as e:
        result["sdk_flush"] = f"ERROR: {e}"

    # ── 3. CallbackHandler creation — langfuse.langchain (v3 API) ─────────
    try:
        from langfuse.langchain import CallbackHandler
        result["callback_import"] = "langfuse.langchain"

        # v3: no constructor args; context via LangChain metadata
        handler = CallbackHandler()
        result["callback_handler"] = "OK"
        result["handler_type"] = type(handler).__name__
    except Exception as e:
        result["callback_handler"] = f"ERROR: {e}"

    # ── 4. langfuse package version ───────────────────────────────────────
    try:
        import importlib.metadata
        result["langfuse_version"] = importlib.metadata.version("langfuse")
    except Exception:
        result["langfuse_version"] = "unknown"

    result["verdict"] = "OK" if result.get("callback_handler") == "OK" else "FAIL"
    return result
