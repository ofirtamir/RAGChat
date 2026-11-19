"""Retrieval evaluation endpoints for the owner's PC (/api/worker/eval/*).

scripts/eval_retrieval.py runs a question set with known answers against the
real index — which lives only on this server — and scores the rankings locally.
The admin console runs the same logic on the server (backend/admin_eval_api.py);
both use src/eval_search.py.

- POST /search   rank each question; mode "hybrid" = the library search alone
                 (no LLM; only the question's embedding is paid, ~free), mode
                 "pipeline" = what a chat user gets: query analysis, a hybrid
                 search per search query, rerank (paid Gemini calls).
- POST /sample   random indexed chunks with their metadata, to write questions from.
- GET  /questions recent real questions, text and time only.

Nothing here touches the question log or usage accounting.
Same shared-secret access as the indexing worker (X-Worker-Key).
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src import eval_search
from .auth import require_worker

router = APIRouter(prefix="/api/worker/eval", dependencies=[Depends(require_worker)])

MAX_ITEMS = 50          # questions per /search call (pipeline mode: keep calls short, ~10)
MAX_K = 50
MAX_SAMPLE = 200
MAX_QUESTIONS = 2000
ITEM_WORKERS = 4        # questions searched in parallel
_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _agent(agent_id: str) -> dict:
    from src.agents import get_agent

    agent = get_agent(agent_id) if _ID_RE.match(agent_id or "") else None
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


class EvalItem(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    filters: dict | None = None


class SearchRequest(BaseModel):
    agent_id: str
    items: list[EvalItem] = Field(min_length=1, max_length=MAX_ITEMS)
    k: int = Field(default=20, ge=1, le=MAX_K)
    mode: Literal["hybrid", "pipeline"] = "hybrid"
    with_meta: bool = True


@router.post("/search")
def eval_search_route(req: SearchRequest):
    """Rank every question; nothing is answered, logged or billed to a user."""
    agent = _agent(req.agent_id)

    def run(item: EvalItem) -> dict:
        return eval_search.search_one(agent, item.query, item.filters, req.mode, req.k, req.with_meta)

    with ThreadPoolExecutor(max_workers=ITEM_WORKERS) as pool:
        outcomes = list(pool.map(run, req.items))
    return {"mode": req.mode, "k": req.k, "items": outcomes}


class SampleRequest(BaseModel):
    agent_id: str
    n: int = Field(default=50, ge=1, le=MAX_SAMPLE)
    seed: int | None = None
    min_chars: int = Field(default=300, ge=0, le=5000)
    exclude: list[str] = Field(default_factory=list, max_length=5000)  # record_keys already used


@router.post("/sample")
def eval_sample(req: SampleRequest):
    """Random chunks (one per decision) to write evaluation questions from."""
    agent = _agent(req.agent_id)
    return {"chunks": eval_search.sample_chunks(agent, req.n, req.seed, req.min_chars, set(req.exclude))}


@router.get("/questions")
def eval_questions(days: int = Query(default=90, ge=1, le=366),
                   limit: int = Query(default=500, ge=1, le=MAX_QUESTIONS)):
    """Recent real questions that went to the library search, newest first,
    each distinct question once. Text and time only: no user, session or answer."""
    return {"questions": eval_search.real_questions(days, limit)}
