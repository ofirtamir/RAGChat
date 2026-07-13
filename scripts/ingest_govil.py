"""Ingest govil-scraper extracted documents into an agent's Chroma collection.

Reads data/<collector>/text/*.json produced by `python -m govil_scraper
extract-text` (local pypdf extraction — no external OCR). Each document is
chunked, every chunk carries the document's full metadata (committee, block,
dates, ...), and chunks are upserted with deterministic ids so re-running
only adds what's new.

Usage:
    python scripts/ingest_govil.py decisive_appraisal_decisions
    python scripts/ingest_govil.py <agent-id> --limit 10   (pilot runs)
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from langchain_core.documents import Document

from src.agents import get_agent, get_agent_vector_store, _date_to_int
from src.chunker import chunk_documents


def _flatten_metadata(agent: dict, record_meta: dict) -> dict:
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("agent_id")
    parser.add_argument("--limit", type=int, help="ingest at most N documents (pilot)")
    parser.add_argument("--min-chars", type=int, default=100,
                        help="skip documents whose extracted text is shorter (scanned PDFs)")
    args = parser.parse_args()

    agent = get_agent(args.agent_id)
    if agent is None:
        raise SystemExit(f"Unknown agent '{args.agent_id}' — run create_govil_agent.py first")

    text_dir = Path(agent["source"]["scraper_root"]) / "data" / agent["source"]["collector"] / "text"
    files = sorted(text_dir.glob("*.json"))
    if not files:
        raise SystemExit(f"No extracted documents in {text_dir} — run govil_scraper extract-text first")
    if args.limit:
        files = files[: args.limit]

    vs = get_agent_vector_store(args.agent_id)
    existing = set()
    got = vs._collection.get(include=[])  # ids only
    for chunk_id in got["ids"]:
        existing.add(chunk_id.rsplit("_", 1)[0])

    ingested = skipped = short = 0
    for path in files:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        record_key = doc["record_key"]
        if record_key in existing:
            skipped += 1
            continue
        text = doc.get("text", "")
        if len(text.strip()) < args.min_chars:
            short += 1
            print(f"  skipping (no text — scanned?): {path.name}")
            continue

        meta = _flatten_metadata(agent, doc.get("metadata", {}))
        meta["record_key"] = record_key
        # 'source' drives citations in the UI — use the human-readable header
        meta["source"] = doc.get("metadata", {}).get(
            next((f["name"] for f in agent["filters"] if "Header" in f["name"]), ""),
            path.stem,
        ) or path.stem

        chunks = chunk_documents([Document(page_content=text, metadata=meta)])
        ids = [f"{record_key}_{i}" for i in range(len(chunks))]
        vs.add_documents(chunks, ids=ids)
        ingested += 1
        if ingested % 10 == 0:
            print(f"  {ingested} documents ingested...")

    print(f"Done: {ingested} ingested, {skipped} already present, {short} skipped (no text).")
    print(f"Collection '{agent['chroma_collection']}' now has {vs._collection.count()} chunks.")


if __name__ == "__main__":
    main()
