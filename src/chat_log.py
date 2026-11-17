"""Question log: one row per question asked in chat, for the admin's quality review.

Each row keeps what the admin needs to judge an answer: the question, how the
pipeline routed it, which chunks reached the answer model (with their search and
rerank scores), the answer itself (gzip), timing, tokens, cost, errors, and the
user's 👍/👎 with an optional comment.

- `access_events` records refused requests (daily quota, monthly budget), so the
  admin can see who runs into the limits.
- Answers older than ANSWER_RETENTION_DAYS are dropped (the row and its numbers stay).

Lives in admin.db next to users and usage_events.
"""
from __future__ import annotations

import gzip
import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from config import DATA_DIR

_DB_PATH = DATA_DIR / "admin.db"
_lock = threading.Lock()

ANSWER_RETENTION_DAYS = 365
MAX_ANSWER_CHARS = 30_000
SLOW_MS = 30_000  # "slow" filter in the console
ROUTES = ("retrieval", "full_doc", "chitchat", "no_docs")

# Phrases the answer model uses when the documents do not hold the answer
# (the answer prompt asks it to say so explicitly). Matched in the first 400 chars.
_NO_ANSWER_RE = re.compile(
    r"לא מצאתי|לא נמצא(?:ו)? (?:מידע|התייחסות|נתונים)|אין (?:בהקשר|במסמכים|מידע|התייחסות)"
    r"|המסמכים (?:אינם|לא) (?:כוללים|מתייחסים|מכילים)|אין לי מספיק מידע|לא ניתן (?:למצוא|לקבוע)"
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS questions (
                id             TEXT PRIMARY KEY,
                ts             TEXT NOT NULL,
                day            TEXT NOT NULL,
                user_sub       TEXT NOT NULL,
                session_id     TEXT,
                agent_id       TEXT,
                query          TEXT NOT NULL,
                filters        TEXT,
                route          TEXT,
                search_queries TEXT,
                retrieved      INTEGER NOT NULL DEFAULT 0,
                retrieval      TEXT,
                answer         BLOB,
                answer_chars   INTEGER NOT NULL DEFAULT 0,
                no_answer      INTEGER NOT NULL DEFAULT 0,
                latency_ms     INTEGER,
                first_token_ms INTEGER,
                input_tokens   INTEGER NOT NULL DEFAULT 0,
                output_tokens  INTEGER NOT NULL DEFAULT 0,
                llm_calls      INTEGER NOT NULL DEFAULT 0,
                cost_usd       REAL NOT NULL DEFAULT 0,
                models         TEXT,
                ok             INTEGER NOT NULL DEFAULT 1,
                error          TEXT,
                rating         INTEGER,
                comment        TEXT,
                feedback_at    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_questions_day ON questions(day);
            CREATE INDEX IF NOT EXISTS idx_questions_user ON questions(user_sub, day);
            CREATE INDEX IF NOT EXISTS idx_questions_rating ON questions(rating);
            CREATE TABLE IF NOT EXISTS access_events (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       TEXT NOT NULL,
                day      TEXT NOT NULL,
                user_sub TEXT NOT NULL,
                reason   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_access_day ON access_events(day);
            """
        )


_init_db()
_last_prune: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_no_answer(answer: str) -> bool:
    return bool(_NO_ANSWER_RE.search((answer or "")[:400]))


def route_of(result: dict | None) -> str | None:
    """retrieval | full_doc | chitchat | no_docs, from a pipeline result."""
    if not result:
        return None
    route, full_doc = result.get("route"), result.get("full_doc_decision")
    if route is not None and not getattr(route, "needs_retrieval", True):
        return "chitchat"
    if full_doc is not None and getattr(full_doc, "needs_full_document", False):
        return "full_doc"
    if not result.get("retrieval") and not result.get("citations"):
        return "no_docs"
    return "retrieval"


# ── writing ──────────────────────────────────────────────────────────────

def log_question(*, question_id: str, user_sub: str, session_id: str | None, agent_id: str | None,
                 query: str, filters: dict | None, result: dict | None, latency_ms: int | None,
                 first_token_ms: int | None, usage, ok: bool, error: str | None = None) -> None:
    """Store one answered (or failed) question. `usage` is a UsageCollector."""
    result = result or {}
    answer = (result.get("answer") or "")[:MAX_ANSWER_CHARS]
    rewritten = result.get("rewritten")
    search_queries = list(getattr(rewritten, "rewritten_queries", None) or [])
    retrieval = result.get("retrieval") or []
    route = route_of(result) if ok else None
    now = _now()
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO questions (id, ts, day, user_sub, session_id, agent_id, query, filters, "
            "route, search_queries, retrieved, retrieval, answer, answer_chars, no_answer, latency_ms, "
            "first_token_ms, input_tokens, output_tokens, llm_calls, cost_usd, models, ok, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (question_id, now.isoformat(), now.strftime("%Y-%m-%d"), user_sub, session_id, agent_id,
             query[:4000], json.dumps(filters or {}, ensure_ascii=False),
             route, json.dumps(search_queries, ensure_ascii=False), len(retrieval),
             json.dumps(retrieval, ensure_ascii=False, default=str),
             gzip.compress(answer.encode("utf-8")) if answer else None, len(answer),
             int(ok and route in ("retrieval", "full_doc") and is_no_answer(answer)),
             latency_ms, first_token_ms, usage.input_tokens, usage.output_tokens, usage.llm_calls,
             usage.cost_usd, ",".join(sorted(usage.models)), int(ok), (error or "")[:1000] or None),
        )
        conn.commit()
    _prune_daily()


def set_feedback(question_id: str, user_sub: str, rating: int, comment: str | None) -> bool:
    """The asker rates their own answer: 1 = 👍, -1 = 👎, 0 = withdraw. False if not theirs."""
    rating = max(-1, min(1, int(rating)))
    with _lock, _connect() as conn:
        cur = conn.execute(
            "UPDATE questions SET rating = ?, comment = ?, feedback_at = ? WHERE id = ? AND user_sub = ?",
            (rating or None, (comment or "").strip()[:2000] or None,
             _now().isoformat() if rating else None, question_id, user_sub),
        )
        conn.commit()
    return cur.rowcount > 0


def record_refusal(user_sub: str, reason: str) -> None:
    now = _now()
    with _lock, _connect() as conn:
        conn.execute("INSERT INTO access_events (ts, day, user_sub, reason) VALUES (?, ?, ?, ?)",
                     (now.isoformat(), now.strftime("%Y-%m-%d"), user_sub, reason))
        conn.commit()


def _prune_daily() -> None:
    global _last_prune
    day = _now().strftime("%Y-%m-%d")
    if _last_prune == day:
        return
    _last_prune = day
    cutoff = (_now() - timedelta(days=ANSWER_RETENTION_DAYS)).strftime("%Y-%m-%d")
    with _lock, _connect() as conn:
        conn.execute("UPDATE questions SET answer = NULL, retrieval = NULL WHERE day < ? AND answer IS NOT NULL",
                     (cutoff,))
        conn.commit()


def new_usage_collector():
    """Token/cost counter for one chat request, priced with the admin's fallback prices."""
    from src import admin_store
    from src.usage_tracker import UsageCollector

    settings = admin_store.get_settings()
    return UsageCollector(fallback_prices=(settings["price_input_per_m"], settings["price_output_per_m"]))


def record_chat(user_id: str, usage, agent_id: str | None, ok: bool, log: dict | None = None) -> None:
    """Usage for billing, plus the question log entry. Never raises: accounting
    must not break an answer."""
    from config import LLM_MODEL
    from src import admin_store

    try:
        admin_store.record_usage(
            user_sub=user_id, kind="chat", model=",".join(sorted(usage.models)) or LLM_MODEL,
            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            llm_calls=usage.llm_calls, agent_id=agent_id, ok=ok, cost_usd=usage.cost_usd,
        )
    except Exception:
        pass
    if log is not None:
        try:
            log_question(user_sub=user_id, agent_id=agent_id, usage=usage, ok=ok, **log)
        except Exception:
            pass


# ── reading ──────────────────────────────────────────────────────────────

_LIST_COLUMNS = ("q.id, q.ts, q.user_sub, q.agent_id, q.query, q.route, q.retrieved, q.answer_chars, "
                 "q.no_answer, q.latency_ms, q.first_token_ms, q.cost_usd, q.ok, q.error, q.rating, "
                 "q.comment, u.email, u.name")

FLAGS = {
    "down": "q.rating = -1",
    "up": "q.rating = 1",
    "feedback": "q.rating IS NOT NULL",
    "no_answer": "q.no_answer = 1",
    "error": "q.ok = 0",
    "slow": f"q.latency_ms >= {SLOW_MS}",
    "no_sources": "q.ok = 1 AND q.route = 'no_docs'",
}


def _since(days: int) -> str:
    return (_now() - timedelta(days=max(1, min(days, 366)) - 1)).strftime("%Y-%m-%d")


def list_questions(days: int = 30, flag: str | None = None, user_sub: str | None = None,
                   q: str | None = None, sort: str = "recent", page: int = 1,
                   page_size: int = 50) -> dict:
    clauses, params = ["q.day >= ?"], [_since(days)]
    if flag:
        if flag not in FLAGS:
            raise ValueError(f"bad flag: {flag}")
        clauses.append(FLAGS[flag])
    if user_sub:
        clauses.append("q.user_sub = ?")
        params.append(user_sub)
    if q and q.strip():
        clauses.append("q.query LIKE ?")
        params.append(f"%{q.strip()}%")
    where = " AND ".join(clauses)
    order = {"recent": "q.ts DESC", "cost": "q.cost_usd DESC", "slow": "q.latency_ms DESC"}.get(sort, "q.ts DESC")
    page, page_size = max(1, page), max(1, min(page_size, 200))
    with _lock, _connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM questions q WHERE {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT {_LIST_COLUMNS} FROM questions q LEFT JOIN users u ON u.sub = q.user_sub "
            f"WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            (*params, page_size, (page - 1) * page_size),
        ).fetchall()
    return {"total": total, "page": page, "page_size": page_size, "questions": [dict(r) for r in rows]}


def get_question(question_id: str) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT q.*, u.email, u.name FROM questions q LEFT JOIN users u ON u.sub = q.user_sub "
            "WHERE q.id = ?", (question_id,),
        ).fetchone()
    if row is None:
        return None
    out = dict(row)
    blob = out.pop("answer")
    out["answer"] = gzip.decompress(blob).decode("utf-8") if blob else None
    for key in ("filters", "search_queries", "retrieval"):
        out[key] = json.loads(out[key]) if out[key] else ([] if key != "filters" else {})
    return out


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


def quality_summary(days: int = 30) -> dict:
    """Answer quality over the last `days` days: feedback, empty answers, errors, speed,
    the documents answers lean on, and a daily series."""
    since = _since(days)
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT day, route, retrieved, no_answer, latency_ms, first_token_ms, ok, rating, retrieval "
            "FROM questions WHERE day >= ?", (since,),
        ).fetchall()
        refusals = conn.execute(
            "SELECT reason, COUNT(*) AS n, COUNT(DISTINCT user_sub) AS users FROM access_events "
            "WHERE day >= ? GROUP BY reason", (since,),
        ).fetchall()
    total = len(rows)
    ok_rows = [r for r in rows if r["ok"]]
    up = sum(1 for r in rows if r["rating"] == 1)
    down = sum(1 for r in rows if r["rating"] == -1)
    routes = {k: 0 for k in ROUTES}
    for r in ok_rows:
        if r["route"] in routes:
            routes[r["route"]] += 1
    latencies = [r["latency_ms"] for r in ok_rows if r["latency_ms"]]
    first_tokens = [r["first_token_ms"] for r in ok_rows if r["first_token_ms"]]

    cited: dict[tuple, dict] = {}
    for r in ok_rows:
        seen = set()
        for c in json.loads(r["retrieval"] or "[]"):
            key = (c.get("record_key"), c.get("source"))
            if not key[0] or key in seen:
                continue
            seen.add(key)
            entry = cited.setdefault(key, {"record_key": key[0], "title": key[1], "questions": 0, "down": 0})
            entry["questions"] += 1
            entry["down"] += r["rating"] == -1

    by_day: dict[str, dict] = {}
    for r in rows:
        d = by_day.setdefault(r["day"], {"day": r["day"], "questions": 0, "up": 0, "down": 0,
                                         "errors": 0, "no_answer": 0})
        d["questions"] += 1
        d["up"] += r["rating"] == 1
        d["down"] += r["rating"] == -1
        d["errors"] += not r["ok"]
        d["no_answer"] += r["no_answer"]
    n_days = max(1, min(days, 366))
    daily = []
    for i in range(n_days):
        day = (_now() - timedelta(days=n_days - 1 - i)).strftime("%Y-%m-%d")
        daily.append(by_day.get(day) or {"day": day, "questions": 0, "up": 0, "down": 0,
                                         "errors": 0, "no_answer": 0})

    return {
        "questions": total,
        "errors": total - len(ok_rows),
        "feedback": up + down,
        "up": up,
        "down": down,
        "satisfaction": up / (up + down) if up + down else None,
        "no_answer": sum(r["no_answer"] for r in ok_rows),
        "no_sources": routes["no_docs"],
        "routes": routes,
        "latency_ms": {"p50": _percentile(latencies, 50), "p95": _percentile(latencies, 95)},
        "first_token_ms": {"p50": _percentile(first_tokens, 50), "p95": _percentile(first_tokens, 95)},
        "slow": sum(1 for v in latencies if v >= SLOW_MS),
        "top_documents": sorted(cited.values(), key=lambda e: -e["questions"])[:15],
        "refusals": [dict(r) for r in refusals],
        "daily": daily,
    }


def user_activity(user_sub: str, days: int = 30) -> dict:
    """Per-user numbers for the user drawer."""
    since = _since(days)
    with _lock, _connect() as conn:
        totals = conn.execute(
            "SELECT COUNT(*) AS questions, COUNT(DISTINCT day) AS active_days, MIN(ts) AS first_ts, "
            "MAX(ts) AS last_ts, COALESCE(SUM(rating = 1), 0) AS up, COALESCE(SUM(rating = -1), 0) AS down, "
            "COALESCE(SUM(no_answer), 0) AS no_answer, COALESCE(SUM(ok = 0), 0) AS errors, "
            "COALESCE(SUM(cost_usd), 0) AS cost FROM questions WHERE user_sub = ?",
            (user_sub,),
        ).fetchone()
        daily = conn.execute(
            "SELECT day, COUNT(*) AS questions FROM questions WHERE user_sub = ? AND day >= ? GROUP BY day",
            (user_sub, since),
        ).fetchall()
        refused = conn.execute(
            "SELECT COUNT(*) FROM access_events WHERE user_sub = ? AND day >= ?", (user_sub, since),
        ).fetchone()[0]
    by_day = {r["day"]: r["questions"] for r in daily}
    n = max(1, min(days, 366))
    series = []
    for i in range(n):
        day = (_now() - timedelta(days=n - 1 - i)).strftime("%Y-%m-%d")
        series.append({"day": day, "questions": by_day.get(day, 0)})
    return {**dict(totals), "refused": refused, "daily": series}
