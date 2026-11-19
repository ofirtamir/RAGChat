"""Embed many library documents through the Gemini Batch API (half the online price).

A request to index many documents becomes one or more batch jobs. Each job
carries the chunks of whole documents, in order; Google runs it within 24
hours. A background poller checks open jobs every minute and, when a job has
finished, writes the vectors to the search index and marks the documents
indexed. The cost per document comes from the token counts Google reports in
the batch result. Jobs are recorded in admin.db, so a restart keeps polling.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone

from config import DATA_DIR
from src import admin_store, library_store

logger = logging.getLogger(__name__)

_DB_PATH = DATA_DIR / "admin.db"
POLL_SECONDS = 60
MAX_JOB_BYTES = 15_000_000   # inline batch requests must stay under 20 MB
_poller: threading.Thread | None = None
_poller_lock = threading.Lock()

_DONE_OK = "SUCCEEDED"
_DONE_BAD = ("FAILED", "CANCELLED", "EXPIRED")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS batch_jobs (
                name       TEXT PRIMARY KEY,
                agent_id   TEXT NOT NULL,
                documents  TEXT NOT NULL,   -- [[record_key, chunk_count], ...] in request order
                state      TEXT NOT NULL,
                error      TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )


_init_db()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jobs(open_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM batch_jobs" + (" WHERE state = 'open'" if open_only else "") + " ORDER BY created_at"
    with _connect() as conn:
        return [dict(r) | {"documents": json.loads(r["documents"])} for r in conn.execute(sql)]


def _set_job(name: str, state: str, error: str | None = None) -> None:
    with _connect() as conn:
        conn.execute("UPDATE batch_jobs SET state = ?, error = ?, updated_at = ? WHERE name = ?",
                     (state, error, _now(), name))


def _plan(agent: dict, keys: list[str]) -> list[list[tuple[dict, list]]]:
    """Group documents into jobs under the size limit: [[(doc, chunks), ...], ...]."""
    from src.library_index import build_chunks, mark_failed

    jobs, current, size = [], [], 0
    for key in keys:
        doc = library_store.get_document(agent["id"], key)
        chunks = build_chunks(doc) if doc and doc.get("parsed_pages") else []
        if not chunks:
            mark_failed(agent["id"], key, "אין טקסט מפורסר")
            continue
        doc_bytes = sum(len(c.text.encode("utf-8")) + 64 for c in chunks)
        if current and size + doc_bytes > MAX_JOB_BYTES:
            jobs.append(current)
            current, size = [], 0
        current.append((doc, chunks))
        size += doc_bytes
    if current:
        jobs.append(current)
    return jobs


def submit(agent_id: str, keys: list[str]) -> list[str]:
    """Create batch jobs for documents already marked 'indexing'. Returns job names."""
    from google.genai import types

    from src.agents import get_agent
    from src.embeddings import MODEL_NAME, embed_config, genai_client
    from src.library_index import mark_failed

    agent = get_agent(agent_id)
    names = []
    for group in _plan(agent, keys):
        texts = [c.text for _, chunks in group for c in chunks]
        try:
            job = genai_client().batches.create_embeddings(
                model=MODEL_NAME,
                src=types.EmbeddingsBatchJobSource(
                    inlined_requests=types.EmbedContentBatch(contents=texts, config=embed_config("RETRIEVAL_DOCUMENT"))),
                config=types.CreateEmbeddingsBatchJobConfig(display_name=f"ragchat-{agent_id}-{len(group)}docs"),
            )
        except Exception as exc:
            logger.exception("batch submit failed")
            for doc, _ in group:
                mark_failed(agent_id, doc["record_key"], exc)
            continue
        documents = [[doc["record_key"], len(chunks)] for doc, chunks in group]
        with _connect() as conn:
            conn.execute("INSERT INTO batch_jobs (name, agent_id, documents, state, created_at, updated_at) "
                         "VALUES (?, ?, ?, 'open', ?, ?)",
                         (job.name, agent_id, json.dumps(documents), _now(), _now()))
        names.append(job.name)
    if names:
        _start_poller()
    return names


def _finish(job_row: dict, job) -> None:
    """Write a finished job's vectors to the index, document by document."""
    from src.agents import get_agent
    from src.embeddings import count_embedding_tokens, normalize
    from src.library_index import build_chunks, mark_failed, store_document
    from src import hybrid_index

    agent = get_agent(job_row["agent_id"])
    price = admin_store.get_settings()["price_embedding_batch_per_m"]
    responses = list(job.dest.inlined_embed_content_responses or [])
    pos = 0
    for key, n in job_row["documents"]:
        part, pos = responses[pos:pos + n], pos + n
        doc = library_store.get_document(agent["id"], key)
        try:
            chunks = build_chunks(doc) if doc else []
            if len(chunks) != n or len(part) != n:
                raise ValueError("החלוקה השתנתה מאז השליחה; יש לאנדקס שוב")
            errors = [r.error for r in part if r.error]
            if errors:
                raise ValueError(str(errors[0])[:200])
            vectors = [normalize(list(r.response.embedding.values)) for r in part]
            counts = [r.response.token_count for r in part]
            tokens = sum(counts) if all(c is not None for c in counts) else \
                count_embedding_tokens([c.text for c in chunks])[0]
            store_document(agent, doc, chunks, vectors, tokens, tokens * price / 1_000_000, "embedding-batch")
        except Exception as exc:
            logger.exception("batch result for %s failed", key)
            mark_failed(agent["id"], key, exc)
    try:
        hybrid_index.maintain(agent)
    except Exception:
        logger.exception("index maintenance failed")


def poll_once() -> None:
    from src.embeddings import genai_client
    from src.library_index import mark_failed

    for row in _jobs():
        try:
            job = genai_client().batches.get(name=row["name"])
        except Exception:
            logger.exception("batch status check failed for %s", row["name"])
            continue
        state = str(getattr(job.state, "name", job.state))
        if state.endswith(_DONE_OK):
            _finish(row, job)
            _set_job(row["name"], "done")
        elif state.endswith(_DONE_BAD):
            message = str(job.error or state)[:300]
            for key, _ in row["documents"]:
                mark_failed(row["agent_id"], key, message)
            _set_job(row["name"], "failed", message)


def _loop() -> None:
    while _jobs():
        time.sleep(POLL_SECONDS)
        try:
            poll_once()
        except Exception:
            logger.exception("batch poller iteration failed")


def _start_poller() -> None:
    global _poller
    with _poller_lock:
        if _poller is None or not _poller.is_alive():
            _poller = threading.Thread(target=_loop, daemon=True, name="batch-poller")
            _poller.start()


def resume() -> set[str]:
    """Called on startup. Returns the record keys that open jobs are waiting for."""
    jobs = _jobs()
    if jobs:
        _start_poller()
    return {key for job in jobs for key, _ in job["documents"]}


def status() -> list[dict]:
    """Open and recent jobs, for the admin console."""
    return [{"name": j["name"], "agent_id": j["agent_id"], "documents": len(j["documents"]),
             "state": j["state"], "error": j["error"], "created_at": j["created_at"]}
            for j in _jobs(open_only=False)[-20:]]
