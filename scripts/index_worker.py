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
    python scripts/index_worker.py run --defer-images  read pictures later, at half price (scripts/image_batch.py)
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


def download_with_retry(client: GovilClient, url: str, attempts: int = 3) -> bytes:
    """gov.il's file server drops a request now and then; try again before failing the document."""
    for attempt in range(1, attempts + 1):
        try:
            return client.download(url)
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(5 * attempt)


def repair_hard_tables(path: Path, parsed_pages, budget: int) -> dict:
    """Re-read the tables the local parser could not read, with a cheap Gemini model.
    Mutates the pages' Markdown in place. Returns {"fixed", "tried", "cost_usd", "fixed_ids"}."""
    import pymupdf

    from src import table_fallback

    out = {"fixed": 0, "tried": 0, "cost_usd": 0.0, "fixed_ids": set()}
    hard = [(p, t) for p in parsed_pages for t in p.tables if table_fallback.is_hard(t)]
    if not hard or budget <= 0:
        return out
    with pymupdf.open(path) as pdf:
        for page, table in hard[:budget]:
            out["tried"] += 1
            try:
                repair = table_fallback.reread_table(pdf[page.number], table)
            except Exception as exc:
                print(f"    Gemini table re-read failed ({str(exc)[:80]}); keeping the local table")
                continue
            out["cost_usd"] += repair.cost_usd
            if repair.markdown:
                page.markdown = page.markdown.replace(
                    table.markdown, table_fallback.replace_table(table.markdown, repair.markdown), 1)
                out["fixed"] += 1
                out["fixed_ids"].add(id(table))
    return out


def _header_before(page, marker: str, by_number: dict) -> str | None:
    """Header row of the table just before a picture: earlier on its page (pictures
    already put in place included), else the last table of the previous page. A
    picture without a header row is usually that table's continuation; the header
    is only borrowed when the column counts match (picture_tables.tidy)."""
    from src import picture_tables

    header = picture_tables.last_header(page.markdown.split(marker, 1)[0])
    previous = by_number.get(page.number - 1)
    return header or (picture_tables.last_header(previous.markdown) if previous else None)


def read_images(path: Path, parsed_pages, budget: int, defer_to: str | None = None) -> dict:
    """Put the content of pictured tables/paragraphs in place of their markers.
    Free filtering and the on-disk cache come first; at most `budget` paid calls.
    With `defer_to` (the document's record key), an unread picture is not read online
    but queued for the half-price Batch API (scripts/image_batch.py); the document
    is parsed again once its pictures are answered.
    Returns {"read", "calls", "cost_usd", "unread": [(page, problem)]}."""
    import pymupdf

    from src import image_batch, image_reader, picture_tables
    from src.pdf_parser import strip_image_markers

    out = {"read": 0, "calls": 0, "cost_usd": 0.0, "unread": []}
    images = [(p, i) for p in parsed_pages for i in p.images]
    if not images:
        return out
    by_number = {p.number: p for p in parsed_pages}
    unavailable = None
    with pymupdf.open(path) as pdf:
        for page, image in images:
            pdf_page, text = pdf[page.number], image_reader.cached(image.digest)
            if text is None:
                text = ""
                if not image_reader.looks_like_document(pdf_page, pymupdf.Rect(image.bbox)):
                    pass                                   # a map / photo / plan: nothing to read
                elif defer_to:
                    image_batch.add_pending(defer_to, path, image)
                    out["unread"].append((page.number, "picture with text waiting for the batch reading"))
                elif unavailable:
                    out["unread"].append((page.number, "picture with text not read (Gemini unavailable)"))
                elif out["calls"] >= budget:
                    out["unread"].append((page.number, "picture with text not read (paid reading off or over the limit)"))
                else:
                    try:
                        reading = image_reader.read_image(pdf_page, image)
                        text = reading.text
                        out["calls"] += 1
                        out["cost_usd"] += reading.cost_usd
                    except Exception as exc:
                        unavailable = str(exc)[:80]
                        print(f"    Gemini image read failed ({unavailable}); this document's other pictures stay unread")
                        out["unread"].append((page.number, "picture with text not read (Gemini unavailable)"))
            if text:
                out["read"] += 1
                text = picture_tables.tidy(text, _header_before(page, image.marker, by_number))
            page.markdown = page.markdown.replace(image.marker, text, 1)
    for page in parsed_pages:
        page.markdown = strip_image_markers(page.markdown)
    return out


def process(doc: dict, client: GovilClient, out_dir: Path, llm_tables: bool = True,
            defer_images: bool = False) -> dict:
    """Download every PDF of one record and parse it locally. Paid Gemini calls are
    used only where the local parse has nothing usable: tables it could not read
    (at most TABLE_FALLBACK_MAX_PER_DOC) and tables/text pasted as pictures (after a
    free filter, at most IMAGE_READER_MAX_PER_DOC; with defer_images, queued for the
    Batch API instead). Returns the /result payload."""
    from src import image_reader, table_fallback
    from src.pdf_parser import parse_pdf, strip_image_markers

    pages, problems, tables = [], [], 0
    fixed, cost = 0, 0.0
    budget = table_fallback.MAX_TABLES_PER_DOCUMENT if llm_tables else 0
    image_budget = image_reader.MAX_IMAGES_PER_DOCUMENT if llm_tables else 0
    pdfs = [f for f in doc["files"] if f.get("ext", "pdf") in ("pdf", "")]
    if not pdfs:
        return {"ok": False, "error": "לרשומה אין קובץ PDF"}
    for i, f in enumerate(pdfs):
        path = out_dir / (f"{doc['record_key']}.pdf" if i == 0 else f"{doc['record_key']}_{i}.pdf")
        if not path.exists() or path.stat().st_size == 0:
            path.write_bytes(download_with_retry(client, f["url"]))
        parsed = parse_pdf(path, image_markers=True)
        repaired = repair_hard_tables(path, parsed, budget)
        budget -= repaired["tried"]
        fixed += repaired["fixed"]
        cost += repaired["cost_usd"]
        pictures = read_images(path, parsed, image_budget,
                               defer_to=doc["record_key"] if defer_images and llm_tables else None)
        image_budget -= pictures["calls"]
        fixed += pictures["read"]
        cost += pictures["cost_usd"]
        unread = {}
        for page_no, problem in pictures["unread"]:
            unread.setdefault(page_no, []).append(problem)
        for page in parsed:
            number = len(pages)
            tables += len(page.tables)
            for t in page.tables:
                if t.problems and id(t) not in repaired["fixed_ids"]:
                    problems.append({"page": number, "problems": t.problems})
            if page.number in unread:
                problems.append({"page": number, "problems": unread[page.number]})
            pages.append({"number": number, "markdown": strip_image_markers(page.markdown)})
    return {"ok": True, "pages": pages, "tables": tables, "problems": problems,
            "llm_tables": fixed, "parse_cost_usd": round(cost, 6)}


def run(agent_id: str, once: bool, llm_tables: bool = True, defer_images: bool = False) -> None:
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
                payload = process(doc, client, out_dir, llm_tables, defer_images)
            except Exception as exc:
                payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:400]}
            payload.update(agent_id=agent_id, record_key=doc["record_key"])
            try:
                status = server.post("result", payload)["status"]
            except Exception as exc:
                status = f"NOT REPORTED ({exc})"
            done += 1
            extra = (f" | {payload['llm_tables']} tables via Gemini ${payload['parse_cost_usd']:.4f}"
                     if payload.get("parse_cost_usd") else "")
            print(f"  [{done}] {status:9} {time.time() - started:4.1f}s  {doc['title'][:80]}{extra}")
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
    p_run.add_argument("--no-llm-tables", action="store_true",
                       help="never send hard tables to Gemini (keep the local parse)")
    p_run.add_argument("--defer-images", action="store_true",
                       help="queue unread pictures for the half-price Batch API (scripts/image_batch.py) "
                            "instead of reading them online")
    p_cat = sub.add_parser("push-catalog")
    p_cat.add_argument("--agent", default=DEFAULT_AGENT)
    p_cat.add_argument("--db", type=Path)
    args = parser.parse_args()
    if args.command == "run":
        llm_tables = not args.no_llm_tables and os.getenv("TABLE_FALLBACK", "1") != "0"
        run(args.agent, args.once, llm_tables, args.defer_images)
    else:
        push_catalog(args.agent, args.db)


if __name__ == "__main__":
    main()
