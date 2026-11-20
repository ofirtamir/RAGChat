"""Admin data: users, usage events and settings (SQLite, next to sessions.db).

- users        — everyone who ever signed in, with an access status and an
                 optional personal daily question limit.
- usage_events — one row per answered question (or indexing run) with token
                 counts and an estimated cost, so usage can be shown per user/day.
- settings     — small key/value store edited from the admin console.

Costs are estimates: tokens reported by the model multiplied by the prices in
settings (USD per 1M tokens). The provider's billing page stays the source of truth.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from config import DATA_DIR

_DB_PATH = DATA_DIR / "admin.db"
_lock = threading.Lock()

USER_STATUSES = ("pending", "approved", "blocked")

DEFAULT_SETTINGS = {
    "require_approval": True,      # new users wait for the admin before they can ask
    "default_daily_limit": 40,     # questions per user per day (0 = unlimited)
    "monthly_budget_usd": 0.0,     # stop answering non-admins above this (0 = off)
    "price_input_per_m": 0.25,     # USD per 1M input tokens, for models not in config.MODEL_PRICES
    "price_output_per_m": 1.50,    # USD per 1M output tokens (incl. thinking), same fallback
    "price_embedding_per_m": 0.15, # USD per 1M embedded tokens (immediate)
    "price_embedding_batch_per_m": 0.075,  # USD per 1M embedded tokens via the Batch API
    "eval_budget_usd": 1.0,        # total the retrieval benchmark may spend (building sets, runs)
    "keyword_spellings": True,     # keyword search also asks for other spellings (קרית/קריית)
}


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
            CREATE TABLE IF NOT EXISTS users (
                sub          TEXT PRIMARY KEY,
                email        TEXT NOT NULL,
                name         TEXT,
                picture      TEXT,
                status       TEXT NOT NULL DEFAULT 'pending',
                daily_limit  INTEGER,
                note         TEXT,
                created_at   TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS usage_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ts            TEXT NOT NULL,
                day           TEXT NOT NULL,
                user_sub      TEXT,
                kind          TEXT NOT NULL,
                model         TEXT,
                input_tokens  INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                llm_calls     INTEGER NOT NULL DEFAULT 0,
                cost_usd      REAL NOT NULL DEFAULT 0,
                agent_id      TEXT,
                ok            INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_usage_day ON usage_events(day);
            CREATE INDEX IF NOT EXISTS idx_usage_user_day ON usage_events(user_sub, day);
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )


_init_db()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def today() -> str:
    return _now().strftime("%Y-%m-%d")


# ── settings ─────────────────────────────────────────────────────────────

def get_settings() -> dict:
    out = dict(DEFAULT_SETTINGS)
    with _lock, _connect() as conn:
        for row in conn.execute("SELECT key, value FROM settings"):
            if row["key"] in out:
                out[row["key"]] = json.loads(row["value"])
    return out


def update_settings(changes: dict) -> dict:
    """Apply known keys only, coerced to the type of the default value."""
    clean = {}
    for key, value in changes.items():
        if key not in DEFAULT_SETTINGS:
            continue
        default = DEFAULT_SETTINGS[key]
        if isinstance(default, bool):
            clean[key] = bool(value)
        elif isinstance(default, int):
            clean[key] = max(0, int(value))
        else:
            clean[key] = max(0.0, float(value))
    with _lock, _connect() as conn:
        for key, value in clean.items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )
        conn.commit()
    return get_settings()


# ── users ────────────────────────────────────────────────────────────────

def touch_user(sub: str, email: str, name: str | None, picture: str | None,
               is_admin: bool = False) -> dict:
    """Register the user on first sight and refresh profile/last-seen after that."""
    now = _now().isoformat()
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE sub = ?", (sub,)).fetchone()
        if row is None:
            status = "approved" if is_admin else "pending"
            conn.execute(
                "INSERT INTO users (sub, email, name, picture, status, created_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sub, email, name, picture, status, now, now),
            )
        else:
            conn.execute(
                "UPDATE users SET email = ?, name = ?, picture = ?, last_seen_at = ? WHERE sub = ?",
                (email, name, picture, now, sub),
            )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE sub = ?", (sub,)).fetchone()
    return dict(row)


def get_user(sub: str) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE sub = ?", (sub,)).fetchone()
    return dict(row) if row else None


def update_user(sub: str, status: str | None = None, daily_limit: int | None = None,
                clear_daily_limit: bool = False, note: str | None = None) -> dict | None:
    sets, params = [], []
    if status is not None:
        if status not in USER_STATUSES:
            raise ValueError(f"bad status: {status}")
        sets.append("status = ?")
        params.append(status)
    if clear_daily_limit:
        sets.append("daily_limit = NULL")
    elif daily_limit is not None:
        sets.append("daily_limit = ?")
        params.append(max(0, int(daily_limit)))
    if note is not None:
        sets.append("note = ?")
        params.append(note[:500])
    if sets:
        with _lock, _connect() as conn:
            conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE sub = ?", (*params, sub))
            conn.commit()
    return get_user(sub)


def list_users() -> list[dict]:
    """All users with their question count and cost for today and this month."""
    day = today()
    month = day[:7]
    with _lock, _connect() as conn:
        rows = conn.execute(
            """
            SELECT u.*,
              COALESCE(SUM(CASE WHEN e.day = ? THEN 1 END), 0)              AS questions_today,
              COALESCE(SUM(CASE WHEN substr(e.day, 1, 7) = ? THEN 1 END), 0) AS questions_month,
              COALESCE(SUM(CASE WHEN substr(e.day, 1, 7) = ? THEN e.cost_usd END), 0) AS cost_month,
              COUNT(e.id)                                                     AS questions_total
            FROM users u
            LEFT JOIN usage_events e ON e.user_sub = u.sub AND e.kind = 'chat'
            GROUP BY u.sub
            ORDER BY u.last_seen_at DESC
            """,
            (day, month, month),
        ).fetchall()
    return [dict(r) for r in rows]


# ── usage ────────────────────────────────────────────────────────────────

def estimate_cost(input_tokens: int, output_tokens: int, settings: dict | None = None) -> float:
    s = settings or get_settings()
    return (input_tokens * s["price_input_per_m"] + output_tokens * s["price_output_per_m"]) / 1_000_000


def record_usage(user_sub: str | None, kind: str, model: str | None, input_tokens: int,
                 output_tokens: int, llm_calls: int = 0, agent_id: str | None = None,
                 ok: bool = True, cost_usd: float | None = None) -> None:
    if cost_usd is None:
        cost_usd = estimate_cost(input_tokens, output_tokens)
    now = _now()
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO usage_events (ts, day, user_sub, kind, model, input_tokens, output_tokens, "
            "llm_calls, cost_usd, agent_id, ok) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now.isoformat(), now.strftime("%Y-%m-%d"), user_sub, kind, model, input_tokens,
             output_tokens, llm_calls, cost_usd, agent_id, int(ok)),
        )
        conn.commit()


def questions_today(user_sub: str) -> int:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM usage_events WHERE user_sub = ? AND day = ? AND kind = 'chat'",
            (user_sub, today()),
        ).fetchone()
    return row[0]


def cost_this_month() -> float:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM usage_events WHERE substr(day, 1, 7) = ?",
            (today()[:7],),
        ).fetchone()
    return float(row[0])


def usage_summary(days: int = 30) -> dict:
    """Daily series for the last `days` days plus totals for today / this month."""
    days = max(1, min(days, 366))
    day = today()
    start = (_now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    with _lock, _connect() as conn:
        daily = conn.execute(
            """
            SELECT day,
                   SUM(kind = 'chat') AS questions,
                   COUNT(DISTINCT CASE WHEN kind = 'chat' THEN user_sub END) AS active_users,
                   SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens,
                   SUM(CASE WHEN kind = 'chat' THEN cost_usd ELSE 0 END) AS chat_cost,
                   SUM(CASE WHEN kind != 'chat' THEN cost_usd ELSE 0 END) AS indexing_cost
            FROM usage_events WHERE day >= ? GROUP BY day ORDER BY day
            """,
            (start,),
        ).fetchall()
        totals = conn.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN day = ? AND kind = 'chat' THEN 1 END), 0) AS questions_today,
              COALESCE(SUM(CASE WHEN day = ? THEN cost_usd END), 0)            AS cost_today,
              COALESCE(SUM(CASE WHEN substr(day,1,7) = ? AND kind = 'chat' THEN 1 END), 0) AS questions_month,
              COALESCE(SUM(CASE WHEN substr(day,1,7) = ? THEN cost_usd END), 0) AS cost_month,
              COALESCE(SUM(CASE WHEN substr(day,1,7) = ? AND kind != 'chat' THEN cost_usd END), 0)
                                                                                AS indexing_cost_month
            FROM usage_events
            """,
            (day, day, day[:7], day[:7], day[:7]),
        ).fetchone()
    by_day = {r["day"]: dict(r) for r in daily}
    series = []
    for i in range(days):
        d = (_now() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        series.append(by_day.get(d) or {
            "day": d, "questions": 0, "active_users": 0, "input_tokens": 0,
            "output_tokens": 0, "chat_cost": 0, "indexing_cost": 0,
        })
    out = dict(totals)
    q_month = out["questions_month"]
    chat_cost_month = out["cost_month"] - out["indexing_cost_month"]
    out["avg_cost_per_question"] = chat_cost_month / q_month if q_month else 0
    out["daily"] = series
    return out
