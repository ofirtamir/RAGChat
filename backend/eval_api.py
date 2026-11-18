"""Retrieval evaluation endpoints for the owner's PC (/api/worker/eval/*).

scripts/eval_retrieval.py runs a question set with known answers against the
real index — which lives only on this server — and scores the rankings locally.

- POST /search   rank each question; mode "hybrid" = the library search alone
                 (no LLM; only the question's embedding is paid, ~free), mode
                 "pipeline" = what a chat user gets: query analysis, a hybrid
                 search per search query, rerank (paid Gemini calls).
- POST /sample   random indexed chunks with their metadata, to write questions from.
- GET  /questions recent real questions, text and time only.

Nothing here touches the question log or usage accounting: those are written
only by the chat endpoints, and these routes call the search functions directly.
Same shared-secret access as the indexing worker (X-Worker-Key).
"""
from __future__ import annotations

import json
import logging
import random
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src import chat_log, hybrid_index, query_analyzer, reranker
from .auth import require_worker

logger = logging.getLogger(__name__)

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


def _sql_list(values) -> str:
    return ", ".join("'" + str(v).replace("'", "''") + "'" for v in values)


# ── index access (thin, replaced in tests) ───────────────────────────────

def _index_keys(agent: dict) -> list[tuple[str, str]]:
    """(chunk id, record_key) of every indexed chunk, without text or vectors."""
    table = hybrid_index._table(agent)
    if table is None:
        return []
    data = hybrid_index._scan(table, ["id", "record_key"])
    return list(zip(data.column("id").to_pylist(), data.column("record_key").to_pylist()))


def _fetch(agent: dict, ids: list[str], columns: list[str]) -> list[dict]:
    table = hybrid_index._table(agent)
    if table is None or not ids:
        return []
    return table.search().where(f"id IN ({_sql_list(ids)})").select(columns).limit(len(ids)).to_list()


def _clean_meta(raw: str | None) -> dict:
    meta = json.loads(raw or "{}")
    return {k: v for k, v in meta.items() if not k.endswith("_int") and k != "record_key"}


def _metas(agent: dict, results: list[dict]) -> dict[str, dict]:
    """{record_key: metadata} for the decisions in a result list (filter accuracy)."""
    one_id = {}
    for r in results:
        one_id.setdefault(r["record_key"], r["id"])
    try:
        rows = _fetch(agent, list(one_id.values()), ["record_key", "meta"])
    except Exception:
        logger.exception("eval: reading result metadata failed")
        return {}
    return {r["record_key"]: _clean_meta(r.get("meta")) for r in rows}


# ── /search ──────────────────────────────────────────────────────────────

class EvalItem(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    filters: dict | None = None


class SearchRequest(BaseModel):
    agent_id: str
    items: list[EvalItem] = Field(min_length=1, max_length=MAX_ITEMS)
    k: int = Field(default=20, ge=1, le=MAX_K)
    mode: Literal["hybrid", "pipeline"] = "hybrid"
    with_meta: bool = True


def _result(row: dict, rank: int | None, stages: list[str], **extra) -> dict:
    return {"rank": rank, "id": row["id"], "record_key": row["record_key"], "section": row.get("section"),
            "chunk_index": row.get("chunk_index"), "page": row.get("page"), "stages": stages,
            "vector_rank": row.get("semantic_rank"), "keyword_rank": row.get("keyword_rank"), **extra}


def _row_stages(row: dict) -> list[str]:
    return [s for s, f in (("vector", "semantic_rank"), ("keyword", "keyword_rank")) if row.get(f)]


def _hybrid(agent: dict, item: EvalItem, k: int) -> dict:
    """The library search on the raw question. Keeps every fused row in the top k,
    plus rows only one method ranked in its own top k (rank None), so the
    vector-only and keyword-only rankings can be scored too."""
    found = hybrid_index.search_stages(agent, item.query, item.filters, k=k)
    results = []
    for row in found["rows"]:
        in_fused = row["fused_rank"] <= k
        own = [s for s, f in (("vector", "semantic_rank"), ("keyword", "keyword_rank"))
               if row.get(f) and row[f] <= k]
        if in_fused or own:
            results.append(_result(row, row["fused_rank"] if in_fused else None,
                                   _row_stages(row) + (["fused"] if in_fused else [])))
    return {"results": results, "errors": found["errors"], "where": found["where"],
            "keyword_terms": found.get("keyword_terms", "")}


def _sub_query(agent: dict, query: str, filters: dict | None) -> dict:
    """One search query as graph.retriever_node runs it (via retriever.retrieve_for_query):
    hybrid search for RETRIEVER_CANDIDATES chunks, then the Gemini rerank to RETRIEVER_K.
    Section expansion is skipped: it never changes which decision a hit belongs to."""
    from config import RETRIEVER_CANDIDATES, RETRIEVER_K

    found = hybrid_index.search_stages(agent, query, filters, k=RETRIEVER_CANDIDATES)
    top = [r for r in found["rows"] if r["in_top_k"]]
    docs = hybrid_index.to_documents(top)
    for doc, row in zip(docs, top):
        doc.metadata["id"] = row["id"]
    kept = reranker.rerank_documents(query, docs, top_k=RETRIEVER_K)
    scores = {d.metadata["id"]: d.metadata.get("_rerank") for d in docs}
    return {"query": query, "top": top, "kept": [d.metadata["id"] for d in kept], "scores": scores,
            "reranked": any(v is not None for v in scores.values()), "errors": found["errors"]}


def _interleave(lists: list[list[str]]) -> list[str]:
    """Round-robin merge without repeats. The chat hands all sub-query results to
    the answer model together; an even interleave is the fairest single order."""
    out, seen = [], set()
    for i in range(max((len(x) for x in lists), default=0)):
        for x in lists:
            if i < len(x) and x[i] not in seen:
                seen.add(x[i])
                out.append(x[i])
    return out


def _pipeline(agent: dict, item: EvalItem) -> dict:
    """What a chat user's question retrieves: analyze_query (no chat history),
    then each search query searched and reranked, as in graph.py."""
    error = None
    try:
        analysis = query_analyzer.analyze_query(item.query, [])
        needs, queries = analysis.needs_retrieval, list(analysis.rewritten_queries or [])
    except Exception as exc:  # graph.analyze_query_node falls back to the raw question
        needs, queries, error = True, [], f"analyzer: {str(exc)[:200]}"
    if not needs:
        return {"route": "chitchat", "search_queries": [], "results": [], "candidates": [], "error": error}
    queries = queries or [item.query]
    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        subs = list(pool.map(lambda q: _sub_query(agent, q, item.filters), queries))

    rows, found_by, kept_by = {}, {}, {}
    for n, sub in enumerate(subs):
        for row in sub["top"]:
            rows.setdefault(row["id"], row)
            found_by.setdefault(row["id"], n)
        for cid in sub["kept"]:
            kept_by.setdefault(cid, n)
    score = {i: s for sub in subs for i, s in sub["scores"].items() if s is not None}

    def build(ids: list[str], kept: bool) -> list[dict]:
        return [_result(rows[i], pos, _row_stages(rows[i]) + ["fused"] + (["reranked"] if i in kept_by else []),
                        sub_query=(kept_by if kept else found_by)[i], rerank_score=score.get(i))
                for pos, i in enumerate(ids, start=1)]

    errors = {f"{n}:{k}": v for n, sub in enumerate(subs) for k, v in sub["errors"].items()}
    return {"route": "retrieval", "search_queries": queries,
            "reranked": all(sub["reranked"] for sub in subs),
            "results": build(_interleave([sub["kept"] for sub in subs]), kept=True),
            "candidates": build(_interleave([[r["id"] for r in sub["top"]] for sub in subs]), kept=False),
            "errors": errors, "error": error}


@router.post("/search")
def eval_search(req: SearchRequest):
    """Rank every question; nothing is answered, logged or billed to a user."""
    agent = _agent(req.agent_id)

    def run(item: EvalItem) -> dict:
        try:
            out = _hybrid(agent, item, req.k) if req.mode == "hybrid" else _pipeline(agent, item)
        except Exception as exc:
            logger.exception("eval search failed")
            return {"query": item.query, "results": [], "error": str(exc)[:300]}
        if req.with_meta:
            out["documents"] = _metas(agent, out["results"])
        return {"query": item.query, **out}

    with ThreadPoolExecutor(max_workers=ITEM_WORKERS) as pool:
        outcomes = list(pool.map(run, req.items))
    return {"mode": req.mode, "k": req.k, "items": outcomes}


# ── /sample ──────────────────────────────────────────────────────────────

class SampleRequest(BaseModel):
    agent_id: str
    n: int = Field(default=50, ge=1, le=MAX_SAMPLE)
    seed: int | None = None
    min_chars: int = Field(default=300, ge=0, le=5000)
    exclude: list[str] = Field(default_factory=list, max_length=5000)  # record_keys already used


def pick_chunks(keys: list[tuple[str, str]], n: int, seed: int | None, tries: int = 3,
                exclude: set[str] | None = None) -> dict[str, list[str]]:
    """Choose n decisions uniformly (not chunks: long decisions would crowd the
    set) and up to `tries` random chunks of each. {record_key: [chunk ids]}."""
    by_doc: dict[str, list[str]] = {}
    for cid, key in keys:
        if key not in (exclude or set()):
            by_doc.setdefault(key, []).append(cid)
    rng = random.Random(seed)
    docs = sorted(by_doc)
    chosen = rng.sample(docs, min(n, len(docs)))
    return {key: rng.sample(sorted(by_doc[key]), min(tries, len(by_doc[key]))) for key in chosen}


@router.post("/sample")
def eval_sample(req: SampleRequest):
    """Random chunks (one per decision) to write evaluation questions from.
    Very short chunks (headings, signatures) are skipped when a longer one exists."""
    agent = _agent(req.agent_id)
    picks = pick_chunks(_index_keys(agent), req.n, req.seed, exclude=set(req.exclude))
    columns = ["id", "record_key", "source", "page", "section", "chunk_index", "text", "meta"]
    rows = {r["id"]: r for r in _fetch(agent, [i for ids in picks.values() for i in ids], columns)}
    chunks = []
    for key, ids in picks.items():
        options = [rows[i] for i in ids if i in rows]
        if not options:
            continue
        row = next((r for r in options if len(r["text"] or "") >= req.min_chars),
                   max(options, key=lambda r: len(r["text"] or "")))
        chunks.append({"id": row["id"], "record_key": key, "source": row.get("source"),
                       "section": row.get("section"), "page": row.get("page"),
                       "chunk_index": row.get("chunk_index"), "text": row["text"],
                       "meta": _clean_meta(row.get("meta"))})
    return {"chunks": chunks}


# ── /questions ───────────────────────────────────────────────────────────

@router.get("/questions")
def eval_questions(days: int = Query(default=90, ge=1, le=366),
                   limit: int = Query(default=500, ge=1, le=MAX_QUESTIONS)):
    """Recent real questions that went to the library search, newest first,
    each distinct question once. Text and time only: no user, session or answer."""
    out, seen, page = [], set(), 1
    while len(out) < limit:
        batch = chat_log.list_questions(days=days, page=page, page_size=200)
        for q in batch["questions"]:
            text = re.sub(r"\s+", " ", q.get("query") or "").strip()
            if not text or q.get("route") == "chitchat" or text in seen:
                continue
            seen.add(text)
            out.append({"ts": q["ts"], "question": text})
        if page * batch["page_size"] >= batch["total"] or page >= 50:
            break
        page += 1
    return {"questions": out[:limit]}
