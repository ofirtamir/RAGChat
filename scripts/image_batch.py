"""Read pictured tables at half price through the Gemini Batch API (src/image_batch.py).

Runs on the indexing machine. Pictures reach the queue in one of two ways:
- the worker, run with --defer-images, queues the pictures of each document it parses;
- `scan` queues the pictures of PDFs already downloaded to this machine.

Usage:
    python scripts/image_batch.py scan [--limit N]     queue pictures of the downloaded PDFs
    python scripts/image_batch.py submit [--limit N]   send the queued pictures as batch jobs
    python scripts/image_batch.py collect [--wait]     store finished answers; re-queue ready documents
    python scripts/image_batch.py status

After `collect`, every document whose pictures are all answered is sent back to
the worker's queue (indexed ones are re-indexed automatically); the worker then
reads the pictures from the cache, with no paid call.
"""
import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from index_worker import DEFAULT_AGENT, Server, pdf_dir  # noqa: E402

from src import image_batch, image_reader  # noqa: E402

WAIT_SECONDS = 300
PER_PICTURE_USD = 0.0011 * image_batch.BATCH_DISCOUNT   # measured on gemini-3.1-flash-lite, 2026-11-17


def record_key(pdf: Path) -> str:
    """'<key>.pdf', or '<key>_<n>.pdf' for the record's other files (see index_worker.process)."""
    m = re.fullmatch(r"(.+)_\d+", pdf.stem)
    return m.group(1) if m and (pdf.parent / f"{m.group(1)}.pdf").exists() else pdf.stem


def scan(agent: str, limit: int | None) -> None:
    from src.pdf_parser import parse_pdf

    pdfs = sorted(pdf_dir(agent).glob("*.pdf"))[:limit]
    added = 0
    for i, pdf in enumerate(pdfs, 1):
        try:
            added += image_batch.collect(record_key(pdf), pdf, parse_pdf(pdf, image_markers=True))
        except Exception as exc:  # noqa: BLE001 — one broken PDF must not stop the scan
            print(f"  {pdf.name}: {type(exc).__name__}: {str(exc)[:100]}")
        if i % 50 == 0:
            print(f"  {i}/{len(pdfs)} PDFs, {added} pictures queued")
    pending = image_batch.status()["pending_pictures"]
    print(f"{len(pdfs)} PDFs scanned, {added} pictures queued. Waiting to be sent: {pending} pictures, "
          f"about ${pending * PER_PICTURE_USD:.2f} at the batch price ({image_reader.MODEL}).")


def submit(limit: int | None) -> None:
    jobs = image_batch.submit(limit=limit)
    for job in jobs:
        print(f"  sent {job['pictures']} pictures: {job['name']}")
    print(f"{len(jobs)} batch jobs sent. Google runs them within 24 hours; then run `collect`."
          if jobs else "Nothing to send.")


def requeue(agent: str, keys: list[str]) -> None:
    if not keys:
        return
    server = Server()
    for start in range(0, len(keys), 500):
        part = keys[start:start + 500]
        queued = server.post("requeue", {"agent_id": agent, "keys": part})["queued"]
        image_batch.clear_ready(part)
        print(f"  {queued} documents sent back to the worker's queue")


def collect(agent: str, wait: bool) -> None:
    while True:
        for job in image_batch.collect_results():
            if job["state"] == "done":
                print(f"  {job['name']}: {job['answered']} answered, {job['failed']} failed, "
                      f"${job['cost_usd']:.4f}, {len(job['ready'])} documents ready")
            else:
                print(f"  {job['name']}: {job['state']} ({job['error']}); its pictures will be sent again")
        requeue(agent, image_batch.ready_keys())
        open_jobs = [j for j in image_batch.status()["jobs"] if j["state"] == "open"]
        if not wait or not open_jobs:
            print(f"{len(open_jobs)} jobs still running." if open_jobs else "No jobs running.")
            return
        time.sleep(WAIT_SECONDS)


def show_status() -> None:
    s = image_batch.status()
    print(f"pictures waiting to be sent: {s['pending_pictures']}, in running jobs: {s['in_jobs']}")
    print(f"documents waiting for pictures: {s['waiting_documents']}, ready to parse again: {s['ready_documents']}")
    print(f"spent so far: ${s['spent_usd']:.4f}")
    for j in s["jobs"]:
        print(f"  {j['created_at'][:16]}  {j['state']:6}  {j['pictures']:5} pictures  ${j['cost_usd']:.4f}  "
              f"{j['name']}{'  ' + j['error'] if j['error'] else ''}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent", default=DEFAULT_AGENT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan").add_argument("--limit", type=int)
    sub.add_parser("submit").add_argument("--limit", type=int)
    sub.add_parser("collect").add_argument("--wait", action="store_true")
    sub.add_parser("status")
    args = parser.parse_args()
    if args.command == "scan":
        scan(args.agent, args.limit)
    elif args.command == "submit":
        submit(args.limit)
    elif args.command == "collect":
        collect(args.agent, args.wait)
    else:
        show_status()


if __name__ == "__main__":
    main()
