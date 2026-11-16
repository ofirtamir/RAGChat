"""Embed parsed library documents into an agent's vector store (server side).

Parsing happens on the indexing machine; what arrives here is Markdown per
page. This module chunks it (tables kept whole), embeds and stores the chunks,
one document at a time on a single background thread so embedding calls don't
compete with chat traffic.
"""
from __future__ import annotations

import logging
import queue
import threading

from langchain_core.documents import Document

from src import admin_store, library_store
from src.chunker import chunk_documents

logger = logging.getLogger(__name__)

_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
_thread: threading.Thread | None = None
_thread_lock = threading.Lock()


def build_chunks(agent: dict, doc: dict) -> list[Document]:
    """Chunk a parsed document; every chunk carries the filter metadata and its page."""
    from src.agent_ingest import flatten_metadata, source_name

    meta = flatten_metadata(agent, doc["meta"])
    meta["record_key"] = doc["record_key"]
    meta["source"] = source_name(agent, doc["meta"], doc["title"])
    page_docs = [
        Document(page_content=p["markdown"], metadata={**meta, "page": p["number"]})
        for p in doc.get("parsed_pages", []) if p.get("markdown", "").strip()
    ]
    return chunk_documents(page_docs)


def remove_from_index(agent_id: str, key: str) -> None:
    from src.agents import get_agent_vector_store

    get_agent_vector_store(agent_id)._collection.delete(where={"record_key": key})


def index_document(agent: dict, key: str) -> dict:
    """Chunk + embed one parsed document.

    Returns {"chunks", "tokens", "cost_usd", "exact"}: the embedding tokens are
    counted by Gemini itself (free countTokens call), so the cost is what the
    embedding call is billed, at the embedding price in the admin settings."""
    from src.embeddings import count_embedding_tokens

    from src.agents import get_agent_vector_store

    doc = library_store.get_document(agent["id"], key)
    if doc is None or not doc.get("parsed_pages"):
        raise ValueError("document has no parsed text")
    chunks = build_chunks(agent, doc)
    if not chunks:
        raise ValueError("parsed text produced no chunks")
    remove_from_index(agent["id"], key)  # re-indexing replaces the old chunks
    vs = get_agent_vector_store(agent["id"])
    vs.add_documents(chunks, ids=[f"{key}_{i}" for i in range(len(chunks))])

    tokens, exact = count_embedding_tokens([c.page_content for c in chunks])
    cost = tokens * admin_store.get_settings()["price_embedding_per_m"] / 1_000_000
    admin_store.record_usage(
        user_sub=None, kind="indexing", model="embedding", input_tokens=tokens, output_tokens=0,
        agent_id=agent["id"], cost_usd=cost,
    )
    return {"chunks": len(chunks), "tokens": tokens, "cost_usd": cost, "exact": exact}


def _friendly_error(exc: Exception) -> str:
    text = str(exc)
    if "RESOURCE_EXHAUSTED" in text or "prepayment" in text:
        return "האינדוקס נכשל: נגמר הקרדיט ב-Gemini. יש לטעון קרדיט ב-Google AI Studio ולנסות שוב."
    return f"האינדוקס נכשל: {text}"[:400]


def process(agent_id: str, key: str) -> None:
    """Index one document and record the outcome. A failure keeps the parsed
    Markdown, so the document returns to 'parsed' with the error shown and
    indexing can simply be retried."""
    from src.agents import get_agent

    try:
        agent = get_agent(agent_id)
        if agent is None:
            raise ValueError(f"unknown agent {agent_id}")
        result = index_document(agent, key)
        library_store.set_status(agent_id, key, "indexed", chunks=result["chunks"],
                                 embed_tokens=result["tokens"], index_cost_usd=result["cost_usd"])
    except Exception as exc:
        logger.exception("indexing failed for %s/%s", agent_id, key)
        library_store.set_status(agent_id, key, "parsed", error=_friendly_error(exc))


def _run() -> None:
    while True:
        agent_id, key = _queue.get()
        try:
            process(agent_id, key)
        except Exception:  # keep the indexer thread alive whatever one document does
            logger.exception("indexer crashed on %s/%s", agent_id, key)
        finally:
            _queue.task_done()


def enqueue(agent_id: str, keys: list[str]) -> None:
    """Queue documents (already marked 'indexing') for the background indexer."""
    global _thread
    with _thread_lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_run, daemon=True, name="library-indexer")
            _thread.start()
    for key in keys:
        _queue.put((agent_id, key))


def resume_interrupted() -> None:
    """After a restart, pick up documents left in 'indexing' by the previous process."""
    from src.agents import list_agents

    for agent in list_agents():
        stuck = library_store.search(agent, status="indexing", page_size=200)["documents"]
        if stuck:
            enqueue(agent["id"], [d["record_key"] for d in stuck])
