"""
Per-user chat session persistence using SQLite.

The database file lives in DATA_DIR alongside the Chroma vector store so it
survives container restarts when DATA_DIR is mounted as a volume.

Schema is intentionally minimal — each row holds a whole session with its
messages serialized as JSON. Sessions are loaded as wholes and rewritten
on every update, so a column-per-message layout buys us nothing.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR

_DB_PATH = DATA_DIR / "sessions.db"
_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                title       TEXT NOT NULL,
                messages    TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_user_updated
                ON chat_sessions(user_id, updated_at DESC);
            """
        )


_init_db()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_sessions(user_id: str) -> list[dict]:
    """Return all sessions for a user, newest first, without message bodies."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM chat_sessions "
            "WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_session(session_id: str, user_id: str) -> dict | None:
    """Return the full session including messages, or None if not found / not owned."""
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT id, title, messages, created_at, updated_at FROM chat_sessions "
            "WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "title": row["title"],
        "messages": json.loads(row["messages"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def save_session(
    user_id: str,
    session_id: str,
    title: str,
    messages: list[dict],
) -> dict:
    """Upsert a session. Returns the saved row."""
    now = _now_iso()
    messages_json = json.dumps(messages, ensure_ascii=False)
    with _lock, _connect() as conn:
        # Preserve created_at on update
        existing = conn.execute(
            "SELECT created_at FROM chat_sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()
        created_at = existing["created_at"] if existing else now
        conn.execute(
            """
            INSERT INTO chat_sessions (id, user_id, title, messages, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title      = excluded.title,
                messages   = excluded.messages,
                updated_at = excluded.updated_at
            WHERE chat_sessions.user_id = excluded.user_id
            """,
            (session_id, user_id, title, messages_json, created_at, now),
        )
        conn.commit()
    return {
        "id": session_id,
        "title": title,
        "created_at": created_at,
        "updated_at": now,
    }


def delete_session(session_id: str, user_id: str) -> bool:
    """Delete a session if it belongs to the user. Returns True if deleted."""
    with _lock, _connect() as conn:
        cur = conn.execute(
            "DELETE FROM chat_sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
