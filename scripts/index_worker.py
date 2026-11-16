"""Local indexing worker: downloads and parses the documents the admin queued.

Runs on the indexing machine only (it uses PyMuPDF, which is not installed on
the server). The admin console decides *what* to process; this script does the
download + parsing here and sends the resulting Markdown to the server, which
stores it for review and embeds it into the search index.

Setup (once):
    pip install -r scripts/requirements-indexing.txt
    add to .env:  WORKER_API_URL=https://<backend-host>   WORKER_API_KEY=<same value as on the server>

Usage:
    python scripts/index_worker.py run                 keep processing the queue
    python scripts/index_worker.py run --once          process what is queued and exit
    python scripts/index_worker.py push-catalog        upload the govil-scraper catalog (first load)
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests
from dotenv import load_dotenv

from src.govil_client import GovilClient

load_dotenv(ROOT / ".env")

DEFAULT_AGENT = "decisive_appraisal_decisions"
IDLE_SECONDS = 15
CATALOG_BATCH = 500


class Server:
    def __init__(self):
        self.base = os.getenv("WORKER_API_URL", "http://localhost:8000").rstrip("/")
        key = os.getenv("WORKER_API_KEY")
        if not key:
            raise SystemExit("WORKER_API_KEY is missing — add it to .env (same value as on the server)")
        self.session = requests.Session()
        self.session.headers["X-Worker-Key"] = key

    def post(self, path: str, payload: dict) -> dict:
        resp = self.session.post(f"{self.base}/api/worker/{path}", json=payload, timeout=120)
        if resp.status_code >= 400:
            raise RuntimeError(f"{path}: HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()


def load_agent(agent_id: str) -> dict:
    path = ROOT / "agents" / f"{agent_id}.json"
    if not path.exists():
        raise SystemExit(f"Unknown agent '{agent_id}' (no {path})")
    return json.loads(path.read_text(encoding="utf-8"))


def pdf_dir(agent_id: str) -> Path:
    from config import DATA_DIR

    d = DATA_DIR / "library_pdfs" / agent_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def process(doc: dict, client: GovilClient, out_dir: Path) -> dict:
    """Download every PDF of one record and parse it. Returns the /result payload."""
    from src.pdf_parser import parse_pdf

    pages, problems, tables = [], [], 0
    pdfs = [f for f in doc["files"] if f.get("ext", "pdf") in ("pdf", "")]
    if not pdfs:
        return {"ok": False, "error": "לרשומה אין קובץ PDF"}
    for i, f in enumerate(pdfs):
        path = out_dir / (f"{doc['record_key']}.pdf" if i == 0 else f"{doc['record_key']}_{i}.pdf")
        if not path.exists() or path.stat().st_size == 0:
            path.write_bytes(client.download(f["url"]))
        for page in parse_pdf(path):
            number = len(pages)
            tables += len(page.tables)
            for t in page.tables:
                if t.problems:
                    problems.append({"page": number, "problems": t.problems})
            pages.append({"number": number, "markdown": page.markdown})
    return {"ok": True, "pages": pages, "tables": tables, "problems": problems}


def run(agent_id: str, once: bool) -> None:
    server = Server()
    agent = load_agent(agent_id)
    client = GovilClient(agent["source"]["search_url"], agent["source"]["x_client_id"])
    out_dir = pdf_dir(agent_id)
    print(f"Worker connected to {server.base} — PDFs are kept in {out_dir}")
    done = 0
    while True:
        try:
            docs = server.post("claim", {"agent_id": agent_id, "limit": 3})["documents"]
        except Exception as exc:
            print(f"  server unreachable ({exc}); retrying in {IDLE_SECONDS}s")
            if once:
                raise SystemExit(1)
            time.sleep(IDLE_SECONDS)
            continue
        if not docs:
            if once:
                break
            time.sleep(IDLE_SECONDS)
            continue
        for doc in docs:
            started = time.time()
            try:
                payload = process(doc, client, out_dir)
            except Exception as exc:
                payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:400]}
            payload.update(agent_id=agent_id, record_key=doc["record_key"])
            try:
                status = server.post("result", payload)["status"]
            except Exception as exc:
                status = f"NOT REPORTED ({exc})"
            done += 1
            print(f"  [{done}] {status:9} {time.time() - started:4.1f}s  {doc['title'][:90]}")
    print(f"Queue is empty — {done} documents processed.")


def push_catalog(agent_id: str, db_path: Path | None) -> None:
    server = Server()
    agent = load_agent(agent_id)
    source = agent.get("source", {})
    db_path = db_path or Path(source.get("scraper_root", "")) / "data" / "govil.db"
    if not db_path.is_file():
        raise SystemExit(f"Catalog DB not found: {db_path} (pass --db)")
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT data FROM records WHERE collector = ?", (source.get("collector", agent_id),)
    ).fetchall()
    conn.close()
    print(f"{len(rows)} records in {db_path}")
    added = 0
    for start in range(0, len(rows), CATALOG_BATCH):
        batch = [json.loads(r[0]) for r in rows[start:start + CATALOG_BATCH]]
        added += server.post("catalog", {"agent_id": agent_id, "records": batch})["added"]
        print(f"  {min(start + CATALOG_BATCH, len(rows))}/{len(rows)} sent ({added} new)")
    print(f"Done: {added} new records in the server catalog.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--agent", default=DEFAULT_AGENT)
    p_run.add_argument("--once", action="store_true")
    p_cat = sub.add_parser("push-catalog")
    p_cat.add_argument("--agent", default=DEFAULT_AGENT)
    p_cat.add_argument("--db", type=Path)
    args = parser.parse_args()
    if args.command == "run":
        run(args.agent, args.once)
    else:
        push_catalog(args.agent, args.db)


if __name__ == "__main__":
    main()
