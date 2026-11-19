"""Retrieval benchmark data for the admin console (SQLite, in admin.db).

- eval_sets       a named question set per agent: synthetic (built from sampled
                  chunks), real (imported from the question log) or manual
- eval_questions  one question with its gold decision(s) (record_keys), optional
                  gold section and expected metadata; source synthetic|real|manual
- eval_runs       one measurement of a set: mode, label, status, cost estimate and
                  the aggregate metrics per question type
- eval_results    per question of a run: metrics, rank of the first gold decision
                  (NULL = missed) and the top retrieved decisions
- eval_spend      what every build and run actually cost (kept when a set is
                  deleted), checked against the admin's benchmark budget

Real user questions are stored here only, on the server — never in the repository.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from config import DATA_DIR

_DB_PATH = DATA_DIR / "admin.db"
_lock = threading.Lock()

SET_KINDS = ("synthetic", "real", "manual")
SOURCES = ("synthetic", "real", "manual")
TOP_KEPT = 10  # retrieved results stored per question
INTERRUPTED = "השרת הופעל מחדש באמצע העבודה"


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
            CREATE TABLE IF NOT EXISTS eval_sets (
                id         TEXT PRIMARY KEY,
                agent_id   TEXT NOT NULL,
                name       TEXT NOT NULL,
                kind       TEXT NOT NULL,
                status     TEXT NOT NULL DEFAULT 'ready',
                info       TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS eval_questions (
                id            TEXT PRIMARY KEY,
                set_id        TEXT NOT NULL,
                question      TEXT NOT NULL,
                type          TEXT NOT NULL,
                gold          TEXT NOT NULL DEFAULT '[]',
                gold_section  TEXT,
                expected_meta TEXT NOT NULL DEFAULT '{}',
                source        TEXT NOT NULL,
                origin        TEXT,
                record_key    TEXT,
                chunk_id      TEXT,
                log_id        TEXT,
                asked_at      TEXT,
                created_at    TEXT NOT NULL,
                UNIQUE (set_id, question)
            );
            CREATE INDEX IF NOT EXISTS idx_eval_questions_set ON eval_questions(set_id, type);
            CREATE TABLE IF NOT EXISTS eval_runs (
                id            TEXT PRIMARY KEY,
                agent_id      TEXT NOT NULL,
                set_id        TEXT NOT NULL,
                label         TEXT,
                mode          TEXT NOT NULL,
                k             INTEGER NOT NULL,
                status        TEXT NOT NULL,
                started_at    TEXT NOT NULL,
                finished_at   TEXT,
                n_questions   INTEGER NOT NULL DEFAULT 0,
                done          INTEGER NOT NULL DEFAULT 0,
                cost_estimate REAL NOT NULL DEFAULT 0,
                cost_usd      REAL NOT NULL DEFAULT 0,
                summary       TEXT,
                degraded      INTEGER NOT NULL DEFAULT 0,
                error         TEXT
            );
            CREATE TABLE IF NOT EXISTS eval_results (
                run_id         TEXT NOT NULL,
                question_id    TEXT NOT NULL,
                type           TEXT,
                question       TEXT,
                gold           TEXT,
                metrics        TEXT NOT NULL,
                found_rank     INTEGER,
                top            TEXT,
                error          TEXT,
                search_queries TEXT,
                PRIMARY KEY (run_id, question_id)
            );
            CREATE TABLE IF NOT EXISTS eval_spend (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       TEXT NOT NULL,
                kind     TEXT NOT NULL,
                ref_id   TEXT,
                cost_usd REAL NOT NULL
            );
            """
        )
        have = {row[1] for row in conn.execute("PRAGMA table_info(eval_questions)")}
        for column, ddl in (("answer", "TEXT"), ("verified", "INTEGER")):  # added with the decisions build
            if column not in have:
                conn.execute(f"ALTER TABLE eval_questions ADD COLUMN {column} {ddl}")


_init_db()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _loads(value, default):
    return json.loads(value) if value else default


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


# ── sets ─────────────────────────────────────────────────────────────────

def _set_row(row: sqlite3.Row) -> dict:
    out = dict(row)
    out["info"] = _loads(out.get("info"), {})
    return out


def create_set(agent_id: str, name: str, kind: str, status: str = "ready", info: dict | None = None) -> dict:
    if kind not in SET_KINDS:
        raise ValueError(f"bad set kind: {kind}")
    set_id = _new_id()
    with _lock, _connect() as conn:
        conn.execute("INSERT INTO eval_sets (id, agent_id, name, kind, status, info, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (set_id, agent_id, name.strip()[:80] or kind, kind, status, _dumps(info or {}), _now()))
        conn.commit()
    return get_set(set_id)


def get_set(set_id: str) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM eval_sets WHERE id = ?", (set_id,)).fetchone()
    return _set_row(row) if row else None


def update_set(set_id: str, status: str | None = None, info: dict | None = None) -> None:
    current = get_set(set_id)
    if current is None:
        return
    merged = {**current["info"], **(info or {})}
    with _lock, _connect() as conn:
        conn.execute("UPDATE eval_sets SET status = ?, info = ? WHERE id = ?",
                     (status or current["status"], _dumps(merged), set_id))
        conn.commit()


def list_sets(agent_id: str) -> list[dict]:
    """Every set of the agent with its question counts, newest first."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            """
            SELECT s.*, COUNT(q.id) AS questions,
                   COALESCE(SUM(CASE WHEN q.gold != '[]' OR q.expected_meta != '{}' THEN 1 END), 0) AS labelled
            FROM eval_sets s LEFT JOIN eval_questions q ON q.set_id = s.id
            WHERE s.agent_id = ? GROUP BY s.id ORDER BY s.created_at DESC
            """, (agent_id,)).fetchall()
    return [_set_row(r) for r in rows]


def real_set(agent_id: str) -> dict:
    """The agent's set of imported real questions (created on first use)."""
    with _lock, _connect() as conn:
        row = conn.execute("SELECT id FROM eval_sets WHERE agent_id = ? AND kind = 'real' "
                           "ORDER BY created_at LIMIT 1", (agent_id,)).fetchone()
    return get_set(row[0]) if row else create_set(agent_id, "שאלות אמיתיות", "real")


def delete_set(set_id: str) -> bool:
    with _lock, _connect() as conn:
        runs = [r[0] for r in conn.execute("SELECT id FROM eval_runs WHERE set_id = ?", (set_id,))]
        conn.executemany("DELETE FROM eval_results WHERE run_id = ?", [(r,) for r in runs])
        conn.execute("DELETE FROM eval_runs WHERE set_id = ?", (set_id,))
        conn.execute("DELETE FROM eval_questions WHERE set_id = ?", (set_id,))
        cur = conn.execute("DELETE FROM eval_sets WHERE id = ?", (set_id,))
        conn.commit()
    return cur.rowcount > 0


# ── questions ────────────────────────────────────────────────────────────

def add_questions(set_id: str, items: list[dict], source: str) -> int:
    """Insert questions; a question already in the set is skipped. Returns how many were added."""
    if source not in SOURCES:
        raise ValueError(f"bad source: {source}")
    added, now = 0, _now()
    with _lock, _connect() as conn:
        for item in items:
            text = " ".join(str(item.get("question") or "").split())[:1000]
            if not text:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO eval_questions (id, set_id, question, type, gold, gold_section, "
                "expected_meta, source, origin, record_key, chunk_id, log_id, asked_at, created_at, answer) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_new_id(), set_id, text, item.get("type") or source, _dumps([g for g in item.get("gold") or [] if g]),
                 item.get("gold_section") or None, _dumps(item.get("expected_meta") or {}), source,
                 item.get("origin"), item.get("record_key"), item.get("chunk_id"), item.get("log_id"),
                 item.get("asked_at"), now, _dumps(item["answer"]) if item.get("answer") else None))
            added += cur.rowcount
        conn.commit()
    return added


def _question_row(row: sqlite3.Row | dict) -> dict:
    out = dict(row)
    out["gold"] = _loads(out["gold"], [])
    out["expected_meta"] = _loads(out["expected_meta"], {})
    out["answer"] = _loads(out.get("answer"), None)
    out["verified"] = None if out.get("verified") is None else bool(out["verified"])
    return out


def get_question(question_id: str) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM eval_questions WHERE id = ?", (question_id,)).fetchone()
    return _question_row(row) if row else None


def update_question(question_id: str, gold: list[str] | None = None, gold_section: str | None = None,
                    qtype: str | None = None, verified: bool | None = None,
                    clear_verified: bool = False) -> dict | None:
    """Set the gold decision(s) (an empty list clears them), gold section, type, or
    whether a person checked the stored answer (True = correct, False = wrong)."""
    sets, params = [], []
    if verified is not None or clear_verified:
        sets.append("verified = ?")
        params.append(None if clear_verified else int(verified))
    if gold is not None:
        sets.append("gold = ?")
        params.append(_dumps(list(dict.fromkeys(g for g in gold if g))))
    if gold_section is not None:
        sets.append("gold_section = ?")
        params.append(gold_section.strip()[:200] or None)
    if qtype is not None:
        sets.append("type = ?")
        params.append(qtype.strip()[:40] or "real")
    if sets:
        with _lock, _connect() as conn:
            conn.execute(f"UPDATE eval_questions SET {', '.join(sets)} WHERE id = ?", (*params, question_id))
            conn.commit()
    return get_question(question_id)


def delete_question(question_id: str) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM eval_questions WHERE id = ?", (question_id,))
        conn.commit()
    return cur.rowcount > 0


def scorable_questions(set_id: str) -> list[dict]:
    """The set's questions that have a gold decision or expected metadata, as
    retrieval_eval items ({id, question, type, gold, gold_section, expected_meta})."""
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT * FROM eval_questions WHERE set_id = ? AND (gold != '[]' OR expected_meta != '{}') "
                            "ORDER BY created_at, id", (set_id,)).fetchall()
    return [_question_row(r) for r in rows]


def facets(set_id: str) -> dict:
    """Question counts per type and per source, for the filter chips."""
    with _lock, _connect() as conn:
        types = conn.execute("SELECT type, COUNT(*) FROM eval_questions WHERE set_id = ? GROUP BY type",
                             (set_id,)).fetchall()
        sources = conn.execute("SELECT source, COUNT(*) FROM eval_questions WHERE set_id = ? GROUP BY source",
                               (set_id,)).fetchall()
    return {"types": {r[0]: r[1] for r in types}, "sources": {r[0]: r[1] for r in sources}}


def list_questions(set_id: str, qtype: str | None = None, source: str | None = None, q: str | None = None,
                   run_id: str | None = None, missed: bool = False, unlabelled: bool = False,
                   page: int = 1, page_size: int = 50) -> dict:
    """A page of a set's questions; with run_id, each carries its result in that run.
    missed = the run did not return any gold decision (questions with a gold only)."""
    clauses, params = ["q.set_id = ?"], [set_id]
    if qtype:
        clauses.append("q.type = ?")
        params.append(qtype)
    if source:
        clauses.append("q.source = ?")
        params.append(source)
    if q and q.strip():
        clauses.append("q.question LIKE ?")
        params.append(f"%{q.strip()}%")
    if unlabelled:
        clauses.append("q.gold = '[]' AND q.expected_meta = '{}'")
    if run_id and missed:
        clauses.append("r.run_id IS NOT NULL AND r.found_rank IS NULL AND q.gold != '[]'")
    where = " AND ".join(clauses)
    page, page_size = max(1, page), max(1, min(page_size, 200))
    join = "LEFT JOIN eval_results r ON r.question_id = q.id AND r.run_id = ?"
    join_params = [run_id or ""]
    with _lock, _connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM eval_questions q {join} WHERE {where}",
                             (*join_params, *params)).fetchone()[0]
        rows = conn.execute(
            f"SELECT q.*, r.run_id AS r_run, r.metrics AS r_metrics, r.found_rank AS r_found, r.top AS r_top, "
            f"r.error AS r_error, r.search_queries AS r_queries FROM eval_questions q {join} WHERE {where} "
            "ORDER BY q.created_at DESC, q.id LIMIT ? OFFSET ?",
            (*join_params, *params, page_size, (page - 1) * page_size)).fetchall()
    out = []
    for row in rows:
        item = _question_row({k: v for k, v in dict(row).items() if not k.startswith("r_")})
        item["result"] = None if not row["r_run"] else {
            "found_rank": row["r_found"], "metrics": _loads(row["r_metrics"], {}), "top": _loads(row["r_top"], []),
            "error": row["r_error"], "search_queries": _loads(row["r_queries"], None)}
        out.append(item)
    return {"total": total, "page": page, "page_size": page_size, "questions": out}


# ── runs ─────────────────────────────────────────────────────────────────

def _run_row(row: sqlite3.Row) -> dict:
    out = dict(row)
    out["summary"] = _loads(out.get("summary"), None)
    return out


def create_run(agent_id: str, set_id: str, mode: str, label: str | None, k: int, n_questions: int,
               cost_estimate: float) -> dict:
    run_id = _new_id()
    with _lock, _connect() as conn:
        conn.execute("INSERT INTO eval_runs (id, agent_id, set_id, label, mode, k, status, started_at, "
                     "n_questions, cost_estimate) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)",
                     (run_id, agent_id, set_id, (label or "").strip()[:60] or None, mode, k, _now(),
                      n_questions, cost_estimate))
        conn.commit()
    return get_run(run_id)


def update_run(run_id: str, **fields) -> None:
    allowed = {"status", "finished_at", "done", "summary", "degraded", "error", "cost_usd"}
    clean = {k: (_dumps(v) if k == "summary" else v) for k, v in fields.items() if k in allowed}
    if not clean:
        return
    with _lock, _connect() as conn:
        conn.execute(f"UPDATE eval_runs SET {', '.join(f'{k} = ?' for k in clean)} WHERE id = ?",
                     (*clean.values(), run_id))
        conn.commit()


def finish_run(run_id: str, status: str, summary: dict | None = None, error: str | None = None) -> None:
    update_run(run_id, status=status, finished_at=_now(), error=error,
               **({"summary": summary} if summary is not None else {}))


def get_run(run_id: str) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT r.*, s.name AS set_name FROM eval_runs r LEFT JOIN eval_sets s "
                           "ON s.id = r.set_id WHERE r.id = ?", (run_id,)).fetchone()
    return _run_row(row) if row else None


def list_runs(agent_id: str, limit: int = 100) -> list[dict]:
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT r.*, s.name AS set_name FROM eval_runs r LEFT JOIN eval_sets s "
                            "ON s.id = r.set_id WHERE r.agent_id = ? ORDER BY r.started_at DESC LIMIT ?",
                            (agent_id, max(1, min(limit, 500)))).fetchall()
    return [_run_row(r) for r in rows]


def found_rank(ranking: list[str], gold: list[str]) -> int | None:
    """1-based position of the first result from a gold decision; None = missed."""
    wanted = {g for g in gold if g}
    return next((pos for pos, key in enumerate(ranking, start=1) if key in wanted), None)


def add_results(run_id: str, rows: list[dict]) -> None:
    """rows: {question_id, type, question, gold, metrics, found_rank, top, error, search_queries}."""
    with _lock, _connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO eval_results (run_id, question_id, type, question, gold, metrics, found_rank, "
            "top, error, search_queries) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(run_id, r["question_id"], r.get("type"), r.get("question"), _dumps(r.get("gold") or []),
              _dumps(r.get("metrics") or {}), r.get("found_rank"), _dumps((r.get("top") or [])[:TOP_KEPT]),
              r.get("error"), _dumps(r.get("search_queries")) if r.get("search_queries") is not None else None)
             for r in rows])
        conn.commit()


def run_report(run_id: str) -> dict | None:
    """A run in the shape retrieval_eval.compare expects: {summary, items:[{id, type, question, metrics}]}."""
    run = get_run(run_id)
    if run is None:
        return None
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT question_id, type, question, metrics, found_rank FROM eval_results "
                            "WHERE run_id = ?", (run_id,)).fetchall()
    items = [{"id": r["question_id"], "type": r["type"], "question": r["question"],
              "metrics": _loads(r["metrics"], {}), "found_rank": r["found_rank"]} for r in rows]
    return {**run, "summary": run["summary"] or {}, "items": items}


# ── spending ─────────────────────────────────────────────────────────────

def record_spend(kind: str, ref_id: str | None, cost_usd: float) -> None:
    if cost_usd <= 0:
        return
    with _lock, _connect() as conn:
        conn.execute("INSERT INTO eval_spend (ts, kind, ref_id, cost_usd) VALUES (?, ?, ?, ?)",
                     (_now(), kind, ref_id, float(cost_usd)))
        conn.commit()


def total_spent() -> float:
    with _lock, _connect() as conn:
        return float(conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM eval_spend").fetchone()[0])


def budget() -> dict:
    """The admin's benchmark budget, what was spent so far, and what is left."""
    from src import admin_store

    limit = float(admin_store.get_settings()["eval_budget_usd"])
    spent = total_spent()
    return {"budget_usd": limit, "spent_usd": round(spent, 6), "remaining_usd": round(max(0.0, limit - spent), 6)}


# ── restart ──────────────────────────────────────────────────────────────

def fail_interrupted() -> None:
    """Called at server start: a job that was running when the server stopped is gone."""
    with _lock, _connect() as conn:
        conn.execute("UPDATE eval_runs SET status = 'failed', error = ?, finished_at = ? WHERE status = 'running'",
                     (INTERRUPTED, _now()))
        conn.execute("UPDATE eval_sets SET status = 'partial' WHERE status = 'building'")
        conn.commit()
