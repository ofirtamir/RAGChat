"""Index parsed library documents into the agent's search index (server side).

Parsing happens on the indexing machine; what arrives here is Markdown per
page. This module chunks the whole document by section (src/section_chunker),
embeds the chunks and writes them to the hybrid index (src/hybrid_index).

Two ways to embed:
- immediately, one document at a time on a background thread (small requests);
- through the Gemini Batch API at half the price for large requests
  (src/library_batch), which finishes within 24 hours.
"""
from __future__ import annotations

import logging
import queue
import threading

from config import BATCH_MIN_DOCUMENTS
from src import admin_store, hybrid_index, library_store
from src.section_chunker import Chunk, chunk_parsed_document

logger = logging.getLogger(__name__)

_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
_thread: threading.Thread | None = None
_thread_lock = threading.Lock()


def document_meta(agent: dict, doc: dict) -> dict:
    """Filter metadata stored on every chunk of the document."""
    from src.agent_ingest import flatten_metadata, source_name

    meta = flatten_metadata(agent, doc["meta"])
    meta["record_key"] = doc["record_key"]
    meta["source"] = source_name(agent, doc["meta"], doc["title"])
    return meta


def build_chunks(doc: dict) -> list[Chunk]:
    pages = [(p["number"], p["markdown"]) for p in doc.get("parsed_pages", []) if p.get("markdown", "").strip()]
    return chunk_parsed_document(pages)


def remove_from_index(agent_id: str, key: str) -> None:
    from src.agents import get_agent

    hybrid_index.delete_document(get_agent(agent_id), key)


def store_document(agent: dict, doc: dict, chunks: list[Chunk], vectors: list[list[float]],
                   tokens: int, cost: float, model_note: str) -> None:
    """Write embedded chunks to the index and record the document as indexed."""
    rows = hybrid_index.rows_for(agent, doc["record_key"], document_meta(agent, doc), chunks, vectors)
    hybrid_index.replace_document(agent, doc["record_key"], rows)
    admin_store.record_usage(user_sub=None, kind="indexing", model=model_note, input_tokens=tokens,
                             output_tokens=0, agent_id=agent["id"], cost_usd=cost)
    library_store.set_status(agent["id"], doc["record_key"], "indexed", chunks=len(chunks),
                             embed_tokens=tokens, index_cost_usd=cost)


def index_document(agent: dict, key: str) -> dict:
    """Chunk + embed one parsed document now (full price).

    The embedding tokens are counted by Gemini itself (free countTokens call),
    so the stored cost is what the embedding call is billed."""
    from src.embeddings import count_embedding_tokens, embed_documents

    doc = library_store.get_document(agent["id"], key)
    if doc is None or not doc.get("parsed_pages"):
        raise ValueError("document has no parsed text")
    chunks = build_chunks(doc)
    if not chunks:
        raise ValueError("parsed text produced no chunks")
    texts = [c.text for c in chunks]
    vectors = embed_documents(texts)
    tokens, exact = count_embedding_tokens(texts)
    cost = tokens * admin_store.get_settings()["price_embedding_per_m"] / 1_000_000
    store_document(agent, doc, chunks, vectors, tokens, cost, "embedding")
    return {"chunks": len(chunks), "tokens": tokens, "cost_usd": cost, "exact": exact}


def friendly_error(exc: Exception | str) -> str:
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
        index_document(agent, key)
    except Exception as exc:
        logger.exception("indexing failed for %s/%s", agent_id, key)
        library_store.set_status(agent_id, key, "parsed", error=friendly_error(exc))


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
    """Index documents already marked 'indexing': large requests go through the
    Batch API at half price, small ones are embedded right away."""
    global _thread
    if len(keys) >= BATCH_MIN_DOCUMENTS:
        from src import library_batch

        library_batch.submit(agent_id, keys)
        return
    with _thread_lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_run, daemon=True, name="library-indexer")
            _thread.start()
    for key in keys:
        _queue.put((agent_id, key))


def resume_interrupted() -> None:
    """After a restart: keep polling open batch jobs, and re-index documents left
    in 'indexing' that no batch job is waiting for."""
    from src import library_batch
    from src.agents import list_agents

    waiting = library_batch.resume()
    for agent in list_agents():
        stuck = library_store.search(agent, status="indexing", page_size=200)["documents"]
        keys = [d["record_key"] for d in stuck if d["record_key"] not in waiting]
        if keys:
            enqueue(agent["id"], keys)
