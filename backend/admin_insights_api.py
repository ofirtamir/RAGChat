"""Admin console: quality and insight endpoints (/api/admin/*).

- chunks of one document (from the index, or a free preview of how it would be chunked)
- chunk quality across a library
- search lab: run the hybrid search and see every stage
- question log, answer-quality summary, engagement and cost analytics
- user drill-down, bulk user actions and CSV exports

Same access rule as backend/admin_api: a verified Google token whose email is in ADMIN_EMAILS.
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from src import admin_insights, admin_store, chat_log, library_store
from .admin_api import _agent
from .auth import require_admin

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


# ── chunks ───────────────────────────────────────────────────────────────

@router.get("/library/{agent_id}/{key}/chunks")
def document_chunks(agent_id: str, key: str, preview: bool = False):
    """The document's chunks with quality flags. Indexed documents are read back
    from the search index (exactly what search sees); otherwise, or with
    preview=1, the stored Markdown is chunked now — free, nothing is embedded."""
    from src import hybrid_index, library_index
    from src.chunk_quality import analyse

    agent = _agent(agent_id)
    doc = library_store.get_document(agent_id, key)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    rows, source = [], "preview"
    if doc["status"] == "indexed" and not preview:
        try:
            rows = [{"text": r["text"], "section": r["section"], "page": r["page"],
                     "chunk_index": r["chunk_index"]} for r in hybrid_index.document_chunks(agent, key)]
            source = "index"
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if source == "preview":
        if not doc.get("parsed_pages"):
            return {"source": "none", "chunks": [], "summary": None}
        rows = [{"text": c.text, "section": c.section, "page": c.page} for c in library_index.build_chunks(doc)]
    chunks, summary = analyse(rows)
    return {"source": source, "chunks": chunks, "summary": summary}


@router.get("/chunk-quality/{agent_id}")
def chunk_quality(agent_id: str):
    _agent(agent_id)
    return library_store.chunk_quality(agent_id)


# ── search lab ───────────────────────────────────────────────────────────

class SearchLabRequest(BaseModel):
    agent_id: str
    query: str = Field(min_length=1, max_length=1000)
    filters: dict | None = None
    rerank: bool = False


@router.post("/search-lab")
def search_lab(req: SearchLabRequest):
    """Run the chat's library search on a question without answering it."""
    from config import RETRIEVER_CANDIDATES, RETRIEVER_K
    from src import hybrid_index

    agent = _agent(req.agent_id)
    result = hybrid_index.search_stages(agent, req.query, req.filters, k=RETRIEVER_CANDIDATES)
    result["candidates"] = RETRIEVER_CANDIDATES
    result["final_k"] = RETRIEVER_K
    result["reranked"] = False
    if req.rerank and result["rows"]:
        from src.reranker import rerank_documents

        top = [r for r in result["rows"] if r["in_top_k"]]
        docs = hybrid_index.to_documents(top)
        for doc, row in zip(docs, top):
            doc.metadata["id"] = row["id"]
        kept = rerank_documents(req.query, docs, top_k=RETRIEVER_K)
        scores = {d.metadata["id"]: d.metadata.get("_rerank") for d in docs}
        chosen = {d.metadata["id"]: i + 1 for i, d in enumerate(kept)}
        for row in result["rows"]:
            row["rerank_score"] = scores.get(row["id"])
            row["final_rank"] = chosen.get(row["id"])
        result["reranked"] = any(v is not None for v in scores.values())
    return result


# ── questions and quality ────────────────────────────────────────────────

@router.get("/questions")
def questions(days: int = 30, flag: str | None = None, user: str | None = None, q: str | None = None,
              sort: str = "recent", page: int = 1):
    try:
        return chat_log.list_questions(days=days, flag=flag, user_sub=user, q=q, sort=sort, page=page)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/questions/{question_id}")
def question(question_id: str):
    row = chat_log.get_question(question_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Question not found")
    return row


@router.get("/quality")
def quality(days: int = 30):
    return chat_log.quality_summary(days)


@router.get("/engagement")
def engagement():
    return admin_insights.engagement()


@router.get("/costs")
def costs(days: int = 30):
    return admin_insights.costs(days)


# ── users ────────────────────────────────────────────────────────────────

@router.get("/users/{sub}/activity")
def user_activity(sub: str):
    # list_users carries this month's question count and cost; get_user does not
    user = next((u for u in admin_store.list_users() if u["sub"] == sub), None)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user": user, **chat_log.user_activity(sub),
            "recent": chat_log.list_questions(days=366, user_sub=sub, page_size=30)["questions"]}


class BulkUsers(BaseModel):
    subs: list[str] = Field(min_length=1, max_length=1000)
    status: str


@router.post("/users/bulk")
def users_bulk(req: BulkUsers):
    if req.status not in admin_store.USER_STATUSES:
        raise HTTPException(status_code=400, detail="מצב לא תקין")
    updated = sum(1 for sub in req.subs
                  if admin_store.get_user(sub) and admin_store.update_user(sub, status=req.status))
    return {"updated": updated}


# ── exports ──────────────────────────────────────────────────────────────

def _csv(name: str, header: list[str], rows: list[list]) -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    # BOM so Excel opens Hebrew correctly
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/export/users.csv")
def export_users():
    cols = ["email", "name", "status", "created_at", "last_seen_at", "questions_today", "questions_month",
            "questions_total", "cost_month", "daily_limit", "note"]
    return _csv("users.csv", cols, [[u.get(c) for c in cols] for u in admin_store.list_users()])


@router.get("/export/questions.csv")
def export_questions(days: int = 30, flag: str | None = None):
    cols = ["ts", "email", "name", "query", "route", "retrieved", "no_answer", "latency_ms", "cost_usd",
            "ok", "error", "rating", "comment"]
    rows, page = [], 1
    while True:
        try:
            batch = chat_log.list_questions(days=days, flag=flag, page=page, page_size=200)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        rows += [[q.get(c) for c in cols] for q in batch["questions"]]
        if page * batch["page_size"] >= batch["total"] or page >= 250:
            break
        page += 1
    return _csv("questions.csv", cols, rows)
