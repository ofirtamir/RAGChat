"""Create a RAGChat agent from a govil-scraper collector.

Reads the collector's config (field schema) and its SQLite metadata DB
(distinct values for dropdowns), and writes agents/<id>.json.

Usage:
    python scripts/create_govil_agent.py decisive_appraisal_decisions
    python scripts/create_govil_agent.py <collector> --scraper-root D:\\path\\to\\govil-scraper --name "שם ידידותי"
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).parent.parent
AGENTS_DIR = PROJECT_ROOT / "agents"
DEFAULT_SCRAPER_ROOT = Path(r"C:\Users\User\Documents\GitHub\govil-scraper")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("collector")
    parser.add_argument("--scraper-root", default=str(DEFAULT_SCRAPER_ROOT))
    parser.add_argument("--name", help="display name for the agent (default: collector title)")
    args = parser.parse_args()

    scraper_root = Path(args.scraper_root)
    collector_config_path = scraper_root / "collectors" / f"{args.collector}.json"
    db_path = scraper_root / "data" / "govil.db"
    if not collector_config_path.exists():
        raise SystemExit(f"Collector config not found: {collector_config_path}")
    if not db_path.exists():
        raise SystemExit(f"Scraper DB not found: {db_path} — run a sync first")

    with open(collector_config_path, encoding="utf-8") as f:
        collector = json.load(f)

    conn = sqlite3.connect(db_path)
    filters = []
    for field in collector.get("fields", []):
        ftype = field["type"]
        if ftype == "document" or field["name"] == "SearchText":
            continue
        entry = {"name": field["name"], "label": field["label"], "type": ftype}
        if ftype == "choice":
            rows = conn.execute(
                "SELECT DISTINCT json_extract(data, ?) FROM records WHERE collector = ? "
                "AND json_extract(data, ?) IS NOT NULL ORDER BY 1",
                (f"$.{field['name']}", args.collector, f"$.{field['name']}"),
            ).fetchall()
            entry["values"] = [r[0] for r in rows if r[0] not in (None, "", "אין מידע")]
        filters.append(entry)
    conn.close()

    agent = {
        "id": args.collector,
        "name": args.name or collector.get("title", args.collector),
        "description": f"סוכן ידע על מאגר '{collector.get('title', args.collector)}' מאתר gov.il",
        "chroma_collection": f"agent_{args.collector}",
        "filters": filters,
        "source": {
            "type": "govil-scraper",
            "collector": args.collector,
            "scraper_root": str(scraper_root),
        },
    }

    AGENTS_DIR.mkdir(exist_ok=True)
    out = AGENTS_DIR / f"{args.collector}.json"
    out.write_text(json.dumps(agent, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Agent written to {out}")
    print(f"  name: {agent['name']}")
    print(f"  collection: {agent['chroma_collection']}")
    print(f"  filters: {', '.join(f['name'] for f in filters)}")
    print(f"\nNext step: python scripts/ingest_govil.py {args.collector}")


if __name__ == "__main__":
    main()
