"""Read pictured tables through the Gemini Batch API: half the price of the online call.

Runs on the indexing machine only (it renders PDF regions with PyMuPDF). The
online reader (src/image_reader) answers at once and costs full price; for
reading many documents, the answer can wait, so the pictures go through the
Batch API instead:

1. collect — a picture that passed the free filter and has no cached answer is
   recorded here as pending (PDF path, page, region, document). Nothing is
   rendered or paid yet. The same picture in several documents is one entry.
2. submit  — pending pictures are rendered, written to a JSONL file (one request
   per picture, the same prompt and settings as the online call), uploaded, and
   sent as one batch job per MAX_PER_JOB pictures. Google runs a job within 24 h.
3. collect_results — a finished job's answers go into the online reader's cache
   (image_reads.db), under the same rules (NONE and cut-off answers are kept as
   "nothing to read"). A document whose pictures are all answered is "ready":
   parsing it again reads every picture from the cache, with no paid call.

A request that failed inside a job goes back to pending and is sent again, up to
MAX_ATTEMPTS times. Jobs are recorded in image_batch.db, so a restart keeps track.
"""
from __future__ import annotations

import base64
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR
from src import image_reader

DB_PATH = DATA_DIR / "image_batch.db"
WORK_DIR = DATA_DIR / "image_batch"
MAX_PER_JOB = 2000
MAX_JOB_BYTES = 1_500_000_000       # input files may be up to 2 GB
MAX_ATTEMPTS = 2
BATCH_DISCOUNT = 0.5                 # the Batch API is billed at half the online price
DONE_OK = "SUCCEEDED"
DONE_BAD = ("FAILED", "CANCELLED", "EXPIRED")

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pending (
            digest     TEXT NOT NULL,
            record_key TEXT NOT NULL,
            pdf        TEXT NOT NULL,
            page       INTEGER NOT NULL,
            bbox       TEXT NOT NULL,      -- JSON [x0, y0, x1, y1]
            job        TEXT,               -- the batch job it was sent in; NULL = not sent yet
            attempts   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (digest, record_key));
        CREATE TABLE IF NOT EXISTS jobs (
            name       TEXT PRIMARY KEY,
            pictures   INTEGER NOT NULL,
            state      TEXT NOT NULL,      -- open | done | failed
            cost_usd   REAL NOT NULL DEFAULT 0,
            error      TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ready (
            record_key TEXT PRIMARY KEY,   -- every picture answered; parse it again
            at         TEXT NOT NULL);""")
    return conn


# ---------- 1. collect ----------

def add_pending(record_key: str, pdf: Path | str, image) -> None:
    """Record one picture (a src.pdf_parser.ParsedImage) to be read in the next batch."""
    with _lock, _db() as conn:
        conn.execute("INSERT OR IGNORE INTO pending (digest, record_key, pdf, page, bbox) VALUES (?, ?, ?, ?, ?)",
                     (image.digest, record_key, str(pdf), image.page, json.dumps(list(image.bbox))))
        conn.execute("DELETE FROM ready WHERE record_key = ?", (record_key,))


def collect(record_key: str, pdf: Path, parsed_pages) -> int:
    """Add a parsed PDF's unread document pictures to pending. Returns how many were added."""
    import pymupdf

    added = 0
    with pymupdf.open(pdf) as doc:
        for page in parsed_pages:
            for image in page.images:
                if image_reader.cached(image.digest) is not None:
                    continue
                if not image_reader.looks_like_document(doc[page.number], pymupdf.Rect(image.bbox)):
                    continue
                add_pending(record_key, pdf, image)
                added += 1
    return added


# ---------- 2. submit ----------

def generation_config() -> dict:
    """The online call's settings (src/image_reader.read_image), in the batch file's JSON form."""
    return {"temperature": 0, "max_output_tokens": image_reader.MAX_OUTPUT_TOKENS,
            "media_resolution": "MEDIA_RESOLUTION_HIGH",
            "thinking_config": {"thinking_level": "MINIMAL"}}


def request_line(digest: str, png: bytes) -> str:
    request = {"contents": [{"role": "user", "parts": [
        {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(png).decode("ascii")}},
        {"text": image_reader.PROMPT}]}],
        "generation_config": generation_config()}
    return json.dumps({"key": digest, "request": request}) + "\n"


def _unsent() -> list[sqlite3.Row]:
    """One row per picture not sent yet (the same picture in several documents is sent once)."""
    with _db() as conn:
        return conn.execute("SELECT digest, pdf, page, bbox FROM pending WHERE job IS NULL "
                            "GROUP BY digest ORDER BY pdf, page").fetchall()


def _write_file(rows, path: Path) -> tuple[list[str], int, list[str]]:
    """Render pictures into one JSONL file, up to the job limits.
    Returns (digests written, rows consumed, digests to drop: already answered or PDF gone)."""
    import pymupdf

    written, dropped, size, doc, doc_path, used = [], [], 0, None, None, 0
    with path.open("w", encoding="utf-8") as f:
        try:
            for row in rows:
                if len(written) >= MAX_PER_JOB or size >= MAX_JOB_BYTES:
                    break
                used += 1
                if image_reader.cached(row["digest"]) is not None:   # answered meanwhile (online)
                    dropped.append(row["digest"])
                    continue
                if row["pdf"] != doc_path:
                    if doc is not None:
                        doc.close()
                    doc_path = row["pdf"]
                    doc = pymupdf.open(doc_path) if Path(doc_path).is_file() else None
                if doc is None:
                    dropped.append(row["digest"])
                    continue
                line = request_line(row["digest"], image_reader.render(doc[row["page"]], json.loads(row["bbox"])))
                f.write(line)
                size += len(line)
                written.append(row["digest"])
        finally:
            if doc is not None:
                doc.close()
    return written, used, dropped


def submit(client=None, limit: int | None = None) -> list[dict]:
    """Send pending pictures as batch jobs. Returns [{"name", "pictures"}]."""
    from google.genai import types

    if client is None:
        from src.embeddings import genai_client
        client = genai_client()
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    rows, sent = _unsent()[:limit], []
    while rows:
        path = WORK_DIR / f"requests-{datetime.now():%Y%m%d-%H%M%S}-{len(sent)}.jsonl"
        digests, used, dropped = _write_file(rows, path)
        rows = rows[used:]
        if dropped:
            with _lock, _db() as conn:
                _resolve(conn, set(dropped))
        if not digests:
            path.unlink(missing_ok=True)
            continue
        try:
            uploaded = client.files.upload(file=str(path), config=types.UploadFileConfig(
                mime_type="jsonl", display_name=path.stem))
            job = client.batches.create(model=image_reader.MODEL, src=uploaded.name,
                                        config=types.CreateBatchJobConfig(display_name=f"ragchat-images-{len(digests)}"))
        finally:
            path.unlink(missing_ok=True)
        with _lock, _db() as conn:
            conn.execute("INSERT INTO jobs (name, pictures, state, created_at, updated_at) VALUES (?, ?, 'open', ?, ?)",
                         (job.name, len(digests), _now(), _now()))
            conn.executemany("UPDATE pending SET job = ? WHERE digest = ?", [(job.name, d) for d in digests])
        sent.append({"name": job.name, "pictures": len(digests)})
    return sent


# ---------- 3. collect results ----------

def _field(d: dict, snake: str):
    """Batch output uses the REST field names (camelCase); accept both."""
    camel = snake.split("_")[0] + "".join(w.title() for w in snake.split("_")[1:])
    return d.get(camel, d.get(snake))


def parse_result(line: dict) -> tuple[str | None, float]:
    """One output line -> (Markdown to cache, cost). None = the request failed; send it again."""
    response = line.get("response")
    if not response or line.get("error"):
        return None, 0.0
    candidates = _field(response, "candidates") or []
    first = candidates[0] if candidates else {}
    parts = (first.get("content") or {}).get("parts") or []
    answer = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    finish = str(_field(first, "finish_reason") or "")
    usage = _field(response, "usage_metadata") or {}
    inp = _field(usage, "prompt_token_count") or 0
    out = (_field(usage, "candidates_token_count") or 0) + (_field(usage, "thoughts_token_count") or 0)
    price_in, price_out = image_reader._price(image_reader.MODEL)
    cost = (inp * price_in + out * price_out) / 1_000_000 * BATCH_DISCOUNT
    return image_reader.accept(answer, finish), cost


def _store_answers(job_name: str, lines: list[dict]) -> dict:
    answered, failed, cost = set(), set(), 0.0
    for line in lines:
        digest = line.get("key")
        if not digest:
            continue
        text, c = parse_result(line)
        cost += c
        if text is None:
            failed.add(digest)
        else:
            image_reader.remember(digest, text)
            answered.add(digest)
    with _lock, _db() as conn:
        sent = {r["digest"] for r in conn.execute("SELECT digest FROM pending WHERE job = ?", (job_name,))}
        failed = (failed | sent) - answered  # a request with no output line failed too
        ready = _retry(conn, failed) | _resolve(conn, answered)
        conn.execute("UPDATE jobs SET state = 'done', cost_usd = ?, updated_at = ? WHERE name = ?",
                     (cost, _now(), job_name))
    return {"answered": len(answered), "failed": len(failed), "cost_usd": cost, "ready": sorted(ready)}


def _resolve(conn, digests: set[str]) -> set[str]:
    """Remove answered pictures from pending; documents with nothing left pending become ready."""
    docs = {r["record_key"] for d in digests
            for r in conn.execute("SELECT record_key FROM pending WHERE digest = ?", (d,))}
    conn.executemany("DELETE FROM pending WHERE digest = ?", [(d,) for d in digests])
    still = {r["record_key"] for r in conn.execute("SELECT DISTINCT record_key FROM pending")}
    ready = docs - still
    conn.executemany("INSERT OR REPLACE INTO ready VALUES (?, ?)", [(k, _now()) for k in ready])
    return ready


def _retry(conn, digests) -> set[str]:
    """Failed pictures go back to pending, or are dropped after MAX_ATTEMPTS (they
    stay unread and are reported on the document as a parsing problem).
    Returns the documents that became ready because of it."""
    for d in digests:
        conn.execute("UPDATE pending SET job = NULL, attempts = attempts + 1 WHERE digest = ?", (d,))
    given_up = {r["digest"] for r in conn.execute("SELECT digest FROM pending WHERE attempts >= ?", (MAX_ATTEMPTS,))}
    return _resolve(conn, given_up)


def collect_results(client=None) -> list[dict]:
    """Check open jobs once; store the answers of finished ones. Returns one summary per finished job."""
    if client is None:
        from src.embeddings import genai_client
        client = genai_client()
    with _db() as conn:
        open_jobs = [r["name"] for r in conn.execute("SELECT name FROM jobs WHERE state = 'open' ORDER BY created_at")]
    out = []
    for name in open_jobs:
        job = client.batches.get(name=name)
        state = str(getattr(job.state, "name", job.state))
        if state.endswith(DONE_OK):
            data = client.files.download(file=job.dest.file_name).decode("utf-8")
            lines = [json.loads(ln) for ln in data.splitlines() if ln.strip()]
            out.append({"name": name, "state": "done", **_store_answers(name, lines)})
        elif state.endswith(DONE_BAD):
            error = str(job.error or state)[:300]
            with _lock, _db() as conn:
                sent = [r["digest"] for r in conn.execute("SELECT DISTINCT digest FROM pending WHERE job = ?", (name,))]
                ready = _retry(conn, sent)
                conn.execute("UPDATE jobs SET state = 'failed', error = ?, updated_at = ? WHERE name = ?",
                             (error, _now(), name))
            out.append({"name": name, "state": "failed", "error": error, "ready": sorted(ready)})
    return out


# ---------- status ----------

def ready_keys() -> list[str]:
    with _db() as conn:
        return [r["record_key"] for r in conn.execute("SELECT record_key FROM ready ORDER BY at")]


def clear_ready(keys: list[str]) -> None:
    with _lock, _db() as conn:
        conn.executemany("DELETE FROM ready WHERE record_key = ?", [(k,) for k in keys])


def status() -> dict:
    with _db() as conn:
        one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "pending_pictures": one("SELECT COUNT(DISTINCT digest) FROM pending WHERE job IS NULL"),
            "in_jobs": one("SELECT COUNT(DISTINCT digest) FROM pending WHERE job IS NOT NULL"),
            "waiting_documents": one("SELECT COUNT(DISTINCT record_key) FROM pending"),
            "ready_documents": one("SELECT COUNT(*) FROM ready"),
            "jobs": [dict(r) for r in conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 20")],
            "spent_usd": one("SELECT COALESCE(SUM(cost_usd), 0) FROM jobs"),
        }
