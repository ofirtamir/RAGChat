"""Pull newly published records from the source site into the library catalog.

The source API lists newest first, so we fetch the latest batch and widen it
only while every record in the batch is new. The first full load of a large
catalog is pushed from the indexing machine instead (scripts/index_worker.py
push-catalog), because paging tens of thousands of records here is slow.
"""
from __future__ import annotations

import threading

from src import library_store
from src.govil_client import GovilClient

_lock = threading.Lock()
status: dict = {"running": False, "agent_id": None, "added": 0, "checked": 0, "error": None}

FIRST_BATCH = 100
MAX_BATCH = 2700


def _run(agent: dict) -> None:
    try:
        source = agent["source"]
        client = GovilClient(source["search_url"], source["x_client_id"])
        limit, added = FIRST_BATCH, 0
        while True:
            records = client.latest_records(limit)
            result = library_store.upsert_catalog(agent, records)
            added += result["added"]
            with _lock:
                status.update(added=added, checked=len(records))
            caught_up = result["added"] < len(records) or len(records) < limit
            if caught_up or limit >= MAX_BATCH:
                break
            limit *= 3
    except Exception as exc:
        with _lock:
            status["error"] = str(exc)[:300]
    finally:
        with _lock:
            status["running"] = False


def start(agent: dict) -> dict:
    source = agent.get("source", {})
    if not source.get("search_url") or not source.get("x_client_id"):
        return {"error": "להגדרת הסוכן חסרים search_url / x_client_id"}
    with _lock:
        if status["running"]:
            return {"error": "עדכון קטלוג כבר רץ"}
        status.update(running=True, agent_id=agent["id"], added=0, checked=0, error=None)
    threading.Thread(target=_run, args=(agent,), daemon=True, name="catalog-sync").start()
    return {"started": True}


def get_status() -> dict:
    with _lock:
        return dict(status)
