"""Background benchmark jobs on the server: build a synthetic question set, or run
a set through retrieval and score it. One job at a time, in a daemon thread;
progress is kept in memory (status()) and in the run/set rows. A server restart
loses a running job: eval_store.fail_interrupted() marks it failed at startup.
"""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from src import eval_questions as eq, eval_search, eval_store, retrieval_eval as rev

logger = logging.getLogger(__name__)

ITEM_WORKERS = 4      # questions searched in parallel
BATCH = 20            # questions per progress/result write
BILLING_ERRORS = ("402", "RESOURCE_EXHAUSTED", "PERMISSION_DENIED", "API_KEY_INVALID")

_lock = threading.Lock()
_stop = threading.Event()
_thread: threading.Thread | None = None
_state: dict = {"running": False, "kind": None, "agent_id": None, "set_id": None, "run_id": None,
                "label": None, "done": 0, "total": 0, "spent_usd": 0.0, "started_at": None,
                "finished_at": None, "error": None, "message": None}


class JobBusy(RuntimeError):
    pass


class BudgetExceeded(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def status() -> dict:
    with _lock:
        return dict(_state)


def _update(**fields) -> None:
    with _lock:
        _state.update(fields)


def _start(kind: str, target, args: tuple, **fields) -> None:
    global _thread
    with _lock:
        if _state["running"]:
            raise JobBusy("עבודה אחרת כבר רצה — המתן שתסתיים")
        _stop.clear()
        _state.update(running=True, kind=kind, agent_id=None, set_id=None, run_id=None, label=None, done=0,
                      total=0, spent_usd=0.0, started_at=_now(), finished_at=None, error=None, message=None)
        _state.update(fields)
    _thread = threading.Thread(target=_guard, args=(target, args), daemon=True, name=f"eval-{kind}")
    _thread.start()


def _guard(target, args: tuple) -> None:
    try:
        target(*args)
    except Exception as exc:
        logger.exception("eval job failed")
        _update(error=str(exc)[:300])
    finally:
        _update(running=False, finished_at=_now())


def cancel() -> bool:
    """Ask the running job to stop after the current batch."""
    if not status()["running"]:
        return False
    _stop.set()
    return True


def wait(timeout: float | None = None) -> None:
    """Block until the current job ends (tests and scripts)."""
    if _thread is not None:
        _thread.join(timeout)


# ── budget ───────────────────────────────────────────────────────────────

def check_budget(cost_usd: float) -> dict:
    """Refuse a paid action whose estimate is above what is left of the benchmark budget."""
    b = eval_store.budget()
    if cost_usd > b["remaining_usd"]:
        raise BudgetExceeded(
            f"העלות המשוערת ({_usd(cost_usd)}) גבוהה ממה שנשאר בתקציב הבנצ'מרק "
            f"({_usd(b['remaining_usd'])} מתוך {_usd(b['budget_usd'])}). אפשר להגדיל את התקציב בלשונית.")
    return b


def _usd(value: float) -> str:
    return f"${value:.4f}" if value < 0.01 else f"${value:.2f}"


# ── build ────────────────────────────────────────────────────────────────

def _plan(agent: dict, n: int, seed: int, meta_share: float) -> dict:
    chunks = eval_search.sample_chunks(agent, int(n * eq.SPARE) + 2, seed)
    return eq.plan(chunks, n, meta_share, seed)


def estimate_build(agent: dict, n: int, seed: int = 7, meta_share: float = eq.META_SHARE) -> dict:
    """What a build would make and cost. Reads the index only; spends nothing."""
    planned = _plan(agent, n, seed, meta_share)
    return {"n": n, "seed": seed, "meta_share": meta_share, "sampled": planned["sampled"],
            "meta_questions": len(planned["meta_items"]), "llm_questions": planned["n_llm"],
            "model": planned["model"], "input_tokens": planned["input_tokens"],
            "output_tokens": planned["output_tokens"], "cost_usd": planned["cost_usd"]}


def start_build(agent: dict, name: str, n: int, seed: int = 7, meta_share: float = eq.META_SHARE,
                estimate_usd: float = 0.0) -> dict:
    """Create a set and fill it in the background (paid: the model writes ~60% of it)."""
    if status()["running"]:
        raise JobBusy("עבודה אחרת כבר רצה — המתן שתסתיים")
    remaining = check_budget(estimate_usd)["remaining_usd"]
    qset = eval_store.create_set(agent["id"], name, "synthetic", status="building",
                                 info={"n": n, "seed": seed, "meta_share": meta_share, "model": eq.QUESTION_MODEL})
    try:
        _start("build", _build, (agent, qset["id"], n, seed, meta_share, remaining), agent_id=agent["id"],
               set_id=qset["id"], label=qset["name"], total=n, message="דוגם קטעים מהאינדקס")
    except JobBusy:
        eval_store.delete_set(qset["id"])
        raise
    return qset


def _build(agent: dict, set_id: str, n: int, seed: int, meta_share: float, remaining: float) -> None:
    spent = {"usd": 0.0, "over": False}

    def should_stop(so_far: float) -> bool:
        spent["usd"] = so_far
        spent["over"] = so_far >= remaining  # hard stop: the estimate may be low
        return _stop.is_set() or spent["over"]

    try:
        planned = _plan(agent, n, seed, meta_share)
        eval_store.add_questions(set_id, planned["meta_items"], "synthetic")
        made = len(planned["meta_items"])
        _update(done=made, total=made + planned["n_llm"], message=f"כותב שאלות עם {planned['model']}")

        def on_item(item: dict, so_far: float) -> None:
            eval_store.add_questions(set_id, [item], "synthetic")
            with _lock:
                _state["done"] += 1
                _state["spent_usd"] = round(so_far, 4)

        written = eq.write_questions(planned, on_item=on_item, should_stop=should_stop)
        spent["usd"] = written["spent"]
    except Exception as exc:
        eval_store.update_set(set_id, status="failed", info={"error": str(exc)[:300], "cost_usd": spent["usd"]})
        raise
    finally:
        eval_store.record_spend("build", set_id, spent["usd"])
    stopped = (written["stopped"] or ("התקציב נגמר" if spent["over"] else None)
               or ("נעצר ידנית" if _stop.is_set() else None))
    total = made + len(written["items"])
    eval_store.update_set(set_id, status="partial" if stopped else "ready",
                          info={"cost_usd": round(written["spent"], 6), "error": stopped,
                                "estimate_usd": planned["cost_usd"]})
    _update(spent_usd=round(written["spent"], 4), done=total,
            error=f"Gemini: {written['stopped']}" if written["stopped"] else None,
            message=f"נכתבו {total} שאלות" + (f" (חלקי: {stopped})" if stopped else ""))


# ── run ──────────────────────────────────────────────────────────────────

def run_estimate(set_id: str, mode: str) -> dict:
    """Hybrid: the question embeddings only (~free). Pipeline: analysis and rerank calls too."""
    items = eval_store.scorable_questions(set_id)
    cost = eq.embedding_cost([i["question"] for i in items])
    if mode == "pipeline":
        cost += eq.pipeline_cost(len(items))
    return {"questions": len(items), "mode": mode, "cost_usd": round(cost, 6)}


def start_run(agent: dict, set_id: str, mode: str, label: str | None = None, k: int = 20) -> dict:
    if mode not in eval_search.MODES:
        raise ValueError(f"bad mode: {mode}")
    items = eval_store.scorable_questions(set_id)
    if not items:
        raise ValueError("אין בסט שאלות עם החלטה נכונה מסומנת")
    if status()["running"]:
        raise JobBusy("עבודה אחרת כבר רצה — המתן שתסתיים")
    estimate = run_estimate(set_id, mode)["cost_usd"]
    remaining = check_budget(estimate)["remaining_usd"]
    run = eval_store.create_run(agent["id"], set_id, mode, label, k, len(items), estimate)
    try:
        _start("run", _run, (agent, run["id"], items, mode, k, remaining), agent_id=agent["id"], set_id=set_id,
               run_id=run["id"], label=run["label"], total=len(items), message=f"מריץ {len(items)} שאלות")
    except JobBusy:
        eval_store.finish_run(run["id"], "failed", error="עבודה אחרת כבר רצה")
        raise
    return run


def result_row(item: dict, outcome: dict) -> dict:
    """One question's stored result: metrics, rank of the first gold decision, top decisions."""
    results = outcome.get("results") or []
    errors = outcome.get("errors") or {}
    top = [{"record_key": r.get("record_key"), "section": r.get("section"), "rank": r["rank"]}
           for r in rev.ranked(results)[:eval_store.TOP_KEPT]]
    return {"question_id": item["id"], "type": item.get("type"), "question": item["question"],
            "gold": item.get("gold") or [], "metrics": rev.score_item(item, outcome),
            "found_rank": eval_store.found_rank(rev.doc_ranking(results), item.get("gold") or []),
            "top": top, "search_queries": outcome.get("search_queries"),
            "error": outcome.get("error") or (json.dumps(errors, ensure_ascii=False) if errors else None),
            "degraded": any("semantic" in key for key in errors)}


def _billing_error(rows: list[dict]) -> str | None:
    """The batch's billing/permission error, when every question hit one."""
    messages = [r["error"] or "" for r in rows]
    if rows and all(any(s in m for s in BILLING_ERRORS) for m in messages):
        return messages[0][:300]
    return None


def _run(agent: dict, run_id: str, items: list[dict], mode: str, k: int, remaining: float) -> None:
    from src import admin_store
    from src.usage_tracker import UsageCollector

    settings = admin_store.get_settings()
    usage = UsageCollector(fallback_prices=(settings["price_input_per_m"], settings["price_output_per_m"]))
    scored, degraded, embedded = [], 0, 0.0

    def cost() -> float:
        return round(usage.cost_usd + embedded, 6)

    try:
        with ThreadPoolExecutor(max_workers=ITEM_WORKERS) as pool:
            for start in range(0, len(items), BATCH):
                if _stop.is_set():
                    raise RuntimeError("נעצר ידנית")
                if cost() >= remaining:
                    raise RuntimeError("התקציב נגמר")
                batch = items[start:start + BATCH]
                outcomes = list(pool.map(
                    lambda i: eval_search.search_one(agent, i["question"], None, mode, k, usage=usage), batch))
                embedded += eq.embedding_cost([i["question"] for i in batch]
                                              + [q for o in outcomes for q in o.get("search_queries") or []])
                rows = [result_row(i, o) for i, o in zip(batch, outcomes)]
                eval_store.add_results(run_id, rows)
                scored += [{"type": r["type"], "metrics": r["metrics"], "error": r["error"]} for r in rows]
                degraded += sum(r["degraded"] for r in rows)
                eval_store.update_run(run_id, done=len(scored), degraded=degraded, cost_usd=cost())
                _update(done=len(scored), spent_usd=cost())
                # pipeline mode: a failing analyzer falls back to the raw question and a failing
                # rerank to the search order, so a run on an unpaid key would measure plain search
                if mode == "pipeline" and start == 0 and (billing := _billing_error(rows)):
                    raise RuntimeError(f"Gemini: {billing}")
    except Exception as exc:
        eval_store.finish_run(run_id, "failed", summary=rev.aggregate(scored) if scored else None,
                              error=str(exc)[:300])
        raise
    finally:
        eval_store.update_run(run_id, cost_usd=cost())
        eval_store.record_spend("run", run_id, cost())
    eval_store.finish_run(run_id, "done", summary=rev.aggregate(scored))
    _update(message=f"הריצה הסתיימה: {len(scored)} שאלות" + (f", {degraded} בלי חיפוש סמנטי" if degraded else ""))
