"""Engagement and cost analytics for the admin console.

Read-only queries over admin.db (users, usage_events, questions). Everything is
computed on request: with a few hundred users the tables stay small enough.

- engagement(): active users per day/week/month, how sticky the product is,
  new users per week, weekly retention by first-question cohort, and user
  segments (power / regular / dormant / never asked) with the people in each.
- costs(): spend by kind and model, per-question percentiles, the most expensive
  questions, and the unit numbers needed to project spend at N users.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

from config import DATA_DIR

_DB_PATH = DATA_DIR / "admin.db"

POWER_USER_QUESTIONS = 20  # questions in 30 days
COHORT_WEEKS = 8


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _week_start(d: date) -> date:
    """Weeks start on Sunday (Israeli work week)."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


def engagement() -> dict:
    today = _today()
    since = today - timedelta(days=7 * COHORT_WEEKS + 30)
    with _connect() as conn:
        activity = conn.execute(
            "SELECT DISTINCT user_sub, day FROM usage_events WHERE kind = 'chat' AND user_sub IS NOT NULL "
            "AND day >= ?", (since.isoformat(),),
        ).fetchall()
        firsts = conn.execute(
            "SELECT user_sub, MIN(day) AS first_day, MAX(day) AS last_day FROM usage_events WHERE kind = 'chat' "
            "AND user_sub IS NOT NULL GROUP BY user_sub",
        ).fetchall()
        users = conn.execute("SELECT sub, email, name, status, created_at, last_seen_at FROM users").fetchall()
        q30 = conn.execute(
            "SELECT user_sub, COUNT(*) AS n FROM usage_events WHERE kind = 'chat' AND day >= ? GROUP BY user_sub",
            ((today - timedelta(days=29)).isoformat(),),
        ).fetchall()

    days_by_user: dict[str, set[date]] = {}
    users_by_day: dict[date, set[str]] = {}
    for r in activity:
        d = date.fromisoformat(r["day"])
        days_by_user.setdefault(r["user_sub"], set()).add(d)
        users_by_day.setdefault(d, set()).add(r["user_sub"])

    def active_between(start: date, end: date) -> set[str]:
        out: set[str] = set()
        for d, subs in users_by_day.items():
            if start <= d <= end:
                out |= subs
        return out

    dau = len(users_by_day.get(today, set()))
    wau = len(active_between(today - timedelta(days=6), today))
    mau = len(active_between(today - timedelta(days=29), today))
    avg_dau = sum(len(users_by_day.get(today - timedelta(days=i), set())) for i in range(30)) / 30

    daily_active = [{"day": (today - timedelta(days=29 - i)).isoformat(),
                     "users": len(users_by_day.get(today - timedelta(days=29 - i), set()))} for i in range(30)]

    # new sign-ups per week, and how many of them asked at least one question
    first_day = {r["user_sub"]: date.fromisoformat(r["first_day"]) for r in firsts}
    last_day = {r["user_sub"]: r["last_day"] for r in firsts}
    this_week = _week_start(today)
    weeks = [this_week - timedelta(weeks=COHORT_WEEKS - 1 - i) for i in range(COHORT_WEEKS)]
    signups = []
    for w in weeks:
        joined = [u for u in users if _week_start(date.fromisoformat(u["created_at"][:10])) == w]
        signups.append({"week": w.isoformat(), "signed_up": len(joined),
                        "activated": sum(1 for u in joined if u["sub"] in first_day)})

    # retention: share of each first-question cohort that asked again in week k
    cohorts = []
    for w in weeks:
        members = [s for s, d in first_day.items() if _week_start(d) == w]
        if not members:
            cohorts.append({"week": w.isoformat(), "size": 0, "retention": []})
            continue
        retention = []
        for k in range((this_week - w).days // 7 + 1):
            start = w + timedelta(weeks=k)
            end = start + timedelta(days=6)
            back = sum(1 for s in members if any(start <= d <= end for d in days_by_user.get(s, ())))
            retention.append(back / len(members))
        cohorts.append({"week": w.isoformat(), "size": len(members), "retention": retention})

    # segments of approved users by activity in the last 30 days
    count30 = {r["user_sub"]: r["n"] for r in q30}
    segments: dict[str, list[dict]] = {"power": [], "regular": [], "dormant": [], "never": []}
    for u in users:
        if u["status"] != "approved":
            continue
        n = count30.get(u["sub"], 0)
        if n >= POWER_USER_QUESTIONS:
            seg = "power"
        elif n > 0:
            seg = "regular"
        elif u["sub"] in first_day:
            seg = "dormant"
        else:
            seg = "never"
        segments[seg].append({"sub": u["sub"], "email": u["email"], "name": u["name"], "questions_30d": n,
                              "last_question": last_day.get(u["sub"]), "created_at": u["created_at"]})
    for seg in segments.values():
        seg.sort(key=lambda e: (e["questions_30d"], e["last_question"] or ""), reverse=True)

    approved = sum(1 for u in users if u["status"] == "approved")
    return {
        "dau": dau, "wau": wau, "mau": mau,
        "stickiness": avg_dau / mau if mau else None,
        "approved_users": approved,
        "activation": len({u["sub"] for u in users} & set(first_day)) / len(users) if users else None,
        "daily_active": daily_active,
        "signups": signups,
        "cohorts": cohorts,
        "segments": {k: {"count": len(v), "users": v[:100]} for k, v in segments.items()},
    }


def costs(days: int = 30) -> dict:
    days = max(1, min(days, 366))
    today = _today()
    since = (today - timedelta(days=days - 1)).isoformat()
    with _connect() as conn:
        by_kind = conn.execute(
            "SELECT kind, COUNT(*) AS events, COALESCE(SUM(cost_usd), 0) AS cost, "
            "COALESCE(SUM(input_tokens), 0) AS input_tokens, COALESCE(SUM(output_tokens), 0) AS output_tokens "
            "FROM usage_events WHERE day >= ? GROUP BY kind ORDER BY cost DESC", (since,),
        ).fetchall()
        by_model = conn.execute(
            "SELECT COALESCE(model, '—') AS model, kind, COUNT(*) AS events, COALESCE(SUM(cost_usd), 0) AS cost "
            "FROM usage_events WHERE day >= ? GROUP BY model, kind ORDER BY cost DESC", (since,),
        ).fetchall()
        chat = conn.execute(
            "SELECT user_sub, cost_usd, input_tokens, output_tokens, llm_calls FROM usage_events "
            "WHERE kind = 'chat' AND day >= ?", (since,),
        ).fetchall()
        expensive = conn.execute(
            "SELECT q.id, q.ts, q.query, q.route, q.retrieved, q.cost_usd, q.input_tokens, q.output_tokens, "
            "q.llm_calls, u.email FROM questions q LEFT JOIN users u ON u.sub = q.user_sub "
            "WHERE q.day >= ? ORDER BY q.cost_usd DESC LIMIT 10", (since,),
        ).fetchall()
        route_costs = conn.execute(
            "SELECT COALESCE(route, 'error') AS route, COUNT(*) AS questions, COALESCE(AVG(cost_usd), 0) AS avg_cost, "
            "COALESCE(AVG(input_tokens), 0) AS avg_input, COALESCE(AVG(output_tokens), 0) AS avg_output "
            "FROM questions WHERE day >= ? GROUP BY route", (since,),
        ).fetchall()
        approved = conn.execute("SELECT COUNT(*) FROM users WHERE status = 'approved'").fetchone()[0]

    per_q = [r["cost_usd"] for r in chat]
    per_user: dict[str, float] = {}
    for r in chat:
        if r["user_sub"]:
            per_user[r["user_sub"]] = per_user.get(r["user_sub"], 0.0) + r["cost_usd"]
    active = len(per_user)
    chat_cost = sum(per_q)
    questions = len(per_q)
    month_factor = 30 / days
    return {
        "days": days,
        "by_kind": [dict(r) for r in by_kind],
        "by_model": [dict(r) for r in by_model],
        "by_route": [dict(r) for r in route_costs],
        "per_question": {
            "count": questions, "avg": chat_cost / questions if questions else None,
            "p50": _percentile(per_q, 50), "p90": _percentile(per_q, 90),
            "p99": _percentile(per_q, 99), "max": max(per_q) if per_q else None,
            "avg_input_tokens": sum(r["input_tokens"] for r in chat) / questions if questions else None,
            "avg_output_tokens": sum(r["output_tokens"] for r in chat) / questions if questions else None,
            "avg_llm_calls": sum(r["llm_calls"] for r in chat) / questions if questions else None,
        },
        "unit": {
            # inputs for "what would N users cost per month"
            "active_users": active,
            "approved_users": approved,
            "active_share": active / approved if approved else None,
            "questions_per_active_user_month": questions / active * month_factor if active else None,
            "chat_cost_per_active_user_month": chat_cost / active * month_factor if active else None,
            "user_cost_p90_month": (_percentile(list(per_user.values()), 90) or 0) * month_factor if per_user else None,
        },
        "expensive_questions": [dict(r) for r in expensive],
    }
