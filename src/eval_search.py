"""Retrieval evaluation: rank questions against the real index, sample chunks to
write questions from, and read recent real questions.

Shared by the worker routes (backend/eval_api.py, used by scripts/eval_retrieval.py
on the owner's PC) and the admin benchmark jobs (src/eval_jobs.py, on the server).

- hybrid    the library search alone on the raw question (no LLM; only the
            question's embedding is paid, ~free)
- pipeline  what a chat user gets: query analysis, a hybrid search per search
            query, rerank (paid Gemini calls)

Nothing here touches the question log or usage accounting: those are written only
by the chat endpoints, and these functions call the search functions directly.
"""
from __future__ import annotations

import contextvars
import json
import logging
import random
import re
from concurrent.futures import ThreadPoolExecutor

from src import chat_log, hybrid_index, query_analyzer, reranker

logger = logging.getLogger(__name__)

MODES = ("hybrid", "pipeline")


def _sql_list(values) -> str:
    return ", ".join("'" + str(v).replace("'", "''") + "'" for v in values)


# ── index access ─────────────────────────────────────────────────────────

def index_keys(agent: dict) -> list[tuple[str, str]]:
    """(chunk id, record_key) of every indexed chunk, without text or vectors."""
    table = hybrid_index._table(agent)
    if table is None:
        return []
    data = hybrid_index._scan(table, ["id", "record_key"])
    return list(zip(data.column("id").to_pylist(), data.column("record_key").to_pylist()))


def fetch(agent: dict, ids: list[str], columns: list[str]) -> list[dict]:
    table = hybrid_index._table(agent)
    if table is None or not ids:
        return []
    return table.search().where(f"id IN ({_sql_list(ids)})").select(columns).limit(len(ids)).to_list()


def clean_meta(raw: str | None) -> dict:
    meta = json.loads(raw or "{}")
    return {k: v for k, v in meta.items() if not k.endswith("_int") and k != "record_key"}


def metas(agent: dict, results: list[dict]) -> dict[str, dict]:
    """{record_key: metadata} for the decisions in a result list (filter accuracy)."""
    one_id = {}
    for r in results:
        one_id.setdefault(r["record_key"], r["id"])
    try:
        rows = fetch(agent, list(one_id.values()), ["record_key", "meta"])
    except Exception:
        logger.exception("eval: reading result metadata failed")
        return {}
    return {r["record_key"]: clean_meta(r.get("meta")) for r in rows}


# ── ranking ──────────────────────────────────────────────────────────────

def _result(row: dict, rank: int | None, stages: list[str], **extra) -> dict:
    return {"rank": rank, "id": row["id"], "record_key": row["record_key"], "section": row.get("section"),
            "chunk_index": row.get("chunk_index"), "page": row.get("page"), "stages": stages,
            "vector_rank": row.get("semantic_rank"), "keyword_rank": row.get("keyword_rank"), **extra}


def _row_stages(row: dict) -> list[str]:
    return [s for s, f in (("vector", "semantic_rank"), ("keyword", "keyword_rank")) if row.get(f)]


def hybrid(agent: dict, query: str, filters: dict | None, k: int) -> dict:
    """The library search on the raw question. Keeps every fused row in the top k,
    plus rows only one method ranked in its own top k (rank None), so the
    vector-only and keyword-only rankings can be scored too."""
    found = hybrid_index.search_stages(agent, query, filters, k=k)
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


def interleave(lists: list[list[str]]) -> list[str]:
    """Round-robin merge without repeats. The chat hands all sub-query results to
    the answer model together; an even interleave is the fairest single order."""
    out, seen = [], set()
    for i in range(max((len(x) for x in lists), default=0)):
        for x in lists:
            if i < len(x) and x[i] not in seen:
                seen.add(x[i])
                out.append(x[i])
    return out


def pipeline(agent: dict, query: str, filters: dict | None) -> dict:
    """What a chat user's question retrieves: analyze_query (no chat history),
    then each search query searched and reranked, as in graph.py."""
    error = None
    try:
        analysis = query_analyzer.analyze_query(query, [])
        needs, queries = analysis.needs_retrieval, list(analysis.rewritten_queries or [])
    except Exception as exc:  # graph.analyze_query_node falls back to the raw question
        needs, queries, error = True, [], f"analyzer: {str(exc)[:200]}"
    if not needs:
        return {"route": "chitchat", "search_queries": [], "results": [], "candidates": [], "error": error}
    queries = queries or [query]
    parent = contextvars.copy_context()  # carries a usage collector (search_one) into the worker threads
    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        subs = list(pool.map(lambda q: parent.copy().run(_sub_query, agent, q, filters), queries))

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
            "results": build(interleave([sub["kept"] for sub in subs]), kept=True),
            "candidates": build(interleave([[r["id"] for r in sub["top"]] for sub in subs]), kept=False),
            "errors": errors, "error": error}


def search_one(agent: dict, query: str, filters: dict | None = None, mode: str = "hybrid",
               k: int = 20, with_meta: bool = True, usage=None) -> dict:
    """Rank one question; never raises (a failure is returned as `error`).
    `usage` (a LangChain callback such as usage_tracker.UsageCollector) receives
    every model call made for it — the analyzer and rerank calls in pipeline mode."""
    from langchain_core.runnables.config import var_child_runnable_config

    token = var_child_runnable_config.set({"callbacks": [usage]}) if usage is not None else None
    try:
        out = hybrid(agent, query, filters, k) if mode == "hybrid" else pipeline(agent, query, filters)
    except Exception as exc:
        logger.exception("eval search failed")
        return {"query": query, "results": [], "error": str(exc)[:300]}
    finally:
        if token is not None:
            var_child_runnable_config.reset(token)
    if with_meta:
        out["documents"] = metas(agent, out["results"])
    return {"query": query, **out}


# ── sampling ─────────────────────────────────────────────────────────────

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


def sample_chunks(agent: dict, n: int, seed: int | None, min_chars: int = 300,
                  exclude: set[str] | None = None, keys: list[tuple[str, str]] | None = None) -> list[dict]:
    """Random chunks, one per decision, to write evaluation questions from. Very
    short chunks (headings, signatures) are skipped when a longer one exists."""
    picks = pick_chunks(index_keys(agent) if keys is None else keys, n, seed, exclude=exclude)
    columns = ["id", "record_key", "source", "page", "section", "chunk_index", "text", "meta"]
    rows = {r["id"]: r for r in fetch(agent, [i for ids in picks.values() for i in ids], columns)}
    chunks = []
    for key, ids in picks.items():
        options = [rows[i] for i in ids if i in rows]
        if not options:
            continue
        row = next((r for r in options if len(r["text"] or "") >= min_chars),
                   max(options, key=lambda r: len(r["text"] or "")))
        chunks.append({"id": row["id"], "record_key": key, "source": row.get("source"),
                       "section": row.get("section"), "page": row.get("page"),
                       "chunk_index": row.get("chunk_index"), "text": row["text"],
                       "meta": clean_meta(row.get("meta"))})
    return chunks


def sample_decisions(agent: dict, n: int, seed: int | None,
                     keys: list[tuple[str, str]] | None = None) -> list[dict]:
    """Random indexed decisions with their whole parsed text, to write questions
    from: [{record_key, title, meta, text}] (decisions without stored text skipped)."""
    from src import library_store

    indexed = sorted({key for _, key in (index_keys(agent) if keys is None else keys) if key})
    out = []
    for key in random.Random(seed).sample(indexed, min(n, len(indexed))):
        doc = library_store.get_document(agent["id"], key)
        pages = (doc or {}).get("parsed_pages") or []
        text = "\n\n".join(p.get("markdown") or "" for p in pages).strip()
        if doc and text:
            out.append({"record_key": key, "title": doc.get("title") or "", "meta": doc.get("meta") or {},
                        "text": text})
    return out


def topic_rows(agent: dict, patterns: list[str], limit: int = 20_000) -> list[dict]:
    """Index chunks whose text contains any of the patterns: [{record_key, text, meta}]."""
    table = hybrid_index._table(agent)
    if table is None or not patterns:
        return []
    where = " OR ".join("text LIKE '%" + p.replace("'", "''") + "%'" for p in patterns)
    return table.search().where(where).select(["record_key", "text", "meta"]).limit(limit).to_list()


# ── real questions ───────────────────────────────────────────────────────

def real_questions(days: int = 90, limit: int = 500, with_id: bool = False) -> list[dict]:
    """Recent real questions that went to the library search, newest first, each
    distinct question once. Text and time only (plus the log id when asked):
    no user, session or answer."""
    out, seen, page = [], set(), 1
    while len(out) < limit:
        batch = chat_log.list_questions(days=days, page=page, page_size=200)
        for q in batch["questions"]:
            text = re.sub(r"\s+", " ", q.get("query") or "").strip()
            if not text or q.get("route") == "chitchat" or text in seen:
                continue
            seen.add(text)
            out.append({"ts": q["ts"], "question": text, **({"log_id": q["id"]} if with_id else {})})
        if page * batch["page_size"] >= batch["total"] or page >= 50:
            break
        page += 1
    return out[:limit]
