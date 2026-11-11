"""Server-side ingestion of gov.il documents into an agent's collection.

Used by the API endpoint (production: pulls straight from gov.il) and shares
metadata logic with scripts/ingest_govil.py. One ingestion job at a time.
"""
import threading

from langchain_core.documents import Document

from src.agents import get_agent, get_agent_vector_store, _date_to_int
from src.chunker import chunk_documents
from src.govil_client import GovilClient, record_key, extract_pdf_text

MIN_TEXT_CHARS = 100  # below this the PDF is probably a scan (needs OCR — skipped)

_job_lock = threading.Lock()
job_status: dict = {
    "running": False, "agent_id": None, "total": 0, "done": 0,
    "ingested": 0, "skipped": 0, "failed": 0, "error": None,
}


def flatten_metadata(agent: dict, record_meta: dict) -> dict:
    """Keep only schema fields; add <date>_int companions for range filtering."""
    out = {}
    for field in agent.get("filters", []):
        value = record_meta.get(field["name"])
        if value in (None, ""):
            continue
        if field["type"] == "date":
            out[field["name"]] = str(value)[:10]
            out[f"{field['name']}_int"] = _date_to_int(str(value))
        else:
            out[field["name"]] = str(value)
    return out


def source_name(agent: dict, record_meta: dict, fallback: str) -> str:
    header_field = next((f["name"] for f in agent.get("filters", []) if "Header" in f["name"]), None)
    if header_field and record_meta.get(header_field):
        return str(record_meta[header_field])
    return fallback


def ingest_document_text(agent: dict, key: str, text: str, record_meta: dict) -> int:
    """Chunk + embed one document into the agent's collection. Returns #chunks."""
    vs = get_agent_vector_store(agent["id"])
    meta = flatten_metadata(agent, record_meta)
    meta["record_key"] = key
    meta["source"] = source_name(agent, record_meta, key)
    chunks = chunk_documents([Document(page_content=text, metadata=meta)])
    if not chunks:
        return 0
    vs.add_documents(chunks, ids=[f"{key}_{i}" for i in range(len(chunks))])
    return len(chunks)


def existing_record_keys(agent: dict) -> set[str]:
    vs = get_agent_vector_store(agent["id"])
    got = vs._collection.get(include=[])
    return {chunk_id.rsplit("_", 1)[0] for chunk_id in got["ids"]}


def _run_ingest_latest(agent: dict, limit: int):
    try:
        source = agent.get("source", {})
        client = GovilClient(source["search_url"], source["x_client_id"])
        records = client.latest_records(limit)
        known = existing_record_keys(agent)
        with _job_lock:
            job_status["total"] = len(records)

        for data in records:
            key = record_key(data)
            try:
                if key in known:
                    with _job_lock:
                        job_status["skipped"] += 1
                    continue
                docs = client.document_urls(data)
                text_parts = []
                for doc in docs:
                    if str(doc.get("Extension", "")).lower() not in ("pdf", ""):
                        continue
                    text_parts.append(extract_pdf_text(client.download(doc["FileName"])))
                text = "\n\n".join(t for t in text_parts if t)
                if len(text) < MIN_TEXT_CHARS:
                    with _job_lock:
                        job_status["skipped"] += 1
                    continue
                ingest_document_text(agent, key, text, data)
                with _job_lock:
                    job_status["ingested"] += 1
            except Exception as exc:
                with _job_lock:
                    job_status["failed"] += 1
                    job_status["error"] = str(exc)[:300]
            finally:
                with _job_lock:
                    job_status["done"] += 1
    except Exception as exc:
        with _job_lock:
            job_status["error"] = str(exc)
    finally:
        with _job_lock:
            job_status["running"] = False


def start_ingest_latest(agent_id: str, limit: int) -> dict:
    """Start a background ingestion job. Returns an error dict if one is running."""
    agent = get_agent(agent_id)
    if agent is None:
        return {"error": f"Unknown agent: {agent_id}"}
    source = agent.get("source", {})
    if not source.get("search_url") or not source.get("x_client_id"):
        return {"error": "Agent config is missing source.search_url / source.x_client_id — "
                         "recreate it with scripts/create_govil_agent.py"}
    with _job_lock:
        if job_status["running"]:
            return {"error": "An ingestion job is already running"}
        job_status.update(running=True, agent_id=agent_id, total=0, done=0,
                          ingested=0, skipped=0, failed=0, error=None)
    threading.Thread(target=_run_ingest_latest, args=(agent, limit), daemon=True).start()
    return {"started": True}


def get_job_status() -> dict:
    with _job_lock:
        return dict(job_status)
