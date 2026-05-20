"""
FastAPI backend for RAGChat.
Exposes the LangGraph pipeline via REST endpoints with SSE streaming.
"""

import os
import sys
import uuid
import json
import shutil
import asyncio
from pathlib import Path
from typing import AsyncGenerator
from concurrent.futures import ThreadPoolExecutor
from queue import Queue, Empty

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Add parent to path so we can import src/
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.graph import run_rag_pipeline, stream_rag_pipeline
from src.document_loader import load_document, SUPPORTED_EXTENSIONS
from src.chunker import chunk_documents
from src.vector_store import (
    add_documents,
    get_document_count,
    clear_vector_store,
    list_document_sources,
    get_all_chunks_for_source,
)
from src.observability import initialize_langfuse
from config import UPLOADS_DIR

app = FastAPI(title="RAGChat API", version="1.0.0")

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

_executor = ThreadPoolExecutor(max_workers=4)


class ChatRequest(BaseModel):
    query: str
    chat_history: list[dict] = []
    session_id: str | None = None
    user_id: str | None = None
    user_email: str | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[str]
    citations: list[dict] = []
    route: dict | None
    rewritten: dict | None
    full_doc_decision: dict | None
    plan: dict | None


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Run the RAG pipeline and return the result."""
    session_id = request.session_id or str(uuid.uuid4())
    # Prefer the Google sub as user_id; fall back to email if missing.
    user_id = request.user_id or request.user_email
    try:
        result = run_rag_pipeline(
            query=request.query,
            chat_history=request.chat_history,
            session_id=session_id,
            user_id=user_id,
        )
        return ChatResponse(
            answer=result["answer"],
            sources=result.get("sources", []),
            citations=result.get("citations", []),
            route=result.get("route").model_dump() if result.get("route") else None,
            rewritten=result.get("rewritten").model_dump() if result.get("rewritten") else None,
            full_doc_decision=result.get("full_doc_decision").model_dump() if result.get("full_doc_decision") else None,
            plan=result.get("plan").model_dump() if result.get("plan") else None,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
):
    """Run the sync stream_rag_pipeline in a thread and push events to a queue."""
    try:
        for event in stream_rag_pipeline(
            query=query,
            chat_history=chat_history,
            session_id=session_id,
            user_id=user_id,
        ):
            q.put(event)
        q.put(None)  # sentinel: done
    except Exception as e:
        q.put({"type": "error", "error": str(e)})
        q.put(None)


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest):
    """Stream the RAG pipeline stages as SSE events, flushed in real-time."""
    session_id = request.session_id or str(uuid.uuid4())
    user_id = request.user_id or request.user_email
    import logging
    logging.getLogger("uvicorn.error").warning(
        "[chat_stream] session_id=%s user_id=%s user_email=%s",
        session_id, user_id, request.user_email,
    )

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
        )

        while True:
            # Poll the queue from the async context
            event = await loop.run_in_executor(None, lambda: q.get(timeout=120))

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


@app.post("/api/upload")
async def upload_documents(files: list[UploadFile] = File(...)):
    """Upload and process documents into the vector store."""
    results = []
    for file in files:
        ext = Path(file.filename).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            results.append({"filename": file.filename, "status": "error", "message": f"Unsupported extension: {ext}"})
            continue
        try:
            save_path = UPLOADS_DIR / file.filename
            with open(save_path, "wb") as f:
                shutil.copyfileobj(file.file, f)
            docs = load_document(save_path)
            chunks = chunk_documents(docs)
            add_documents(chunks)
            results.append({"filename": file.filename, "status": "ok", "chunks": len(chunks)})
        except Exception as e:
            results.append({"filename": file.filename, "status": "error", "message": str(e)})
    return {"results": results}


@app.get("/api/documents")
async def get_documents():
    """Return stats and list of indexed documents."""
    try:
        sources = list_document_sources()
        total = get_document_count()
        doc_stats = []
        for src in sources:
            chunks = get_all_chunks_for_source(src)
            doc_stats.append({
                "source": src,
                "chunks": len(chunks),
                "chars": sum(len(c.page_content) for c in chunks),
            })
        return {"total_chunks": total, "documents": doc_stats}
    except Exception:
        return {"total_chunks": 0, "documents": []}


@app.delete("/api/documents")
async def delete_documents():
    """Clear all documents from the vector store."""
    clear_vector_store()
    return {"status": "cleared"}


@app.get("/api/health")
async def health():
    from src.observability import is_langfuse_enabled
    return {
        "status": "ok",
        "langfuse": is_langfuse_enabled(),
    }


@app.get("/api/debug/langfuse")
async def debug_langfuse():
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
