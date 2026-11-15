"""Document library: the catalog of source documents and where each one stands.

One row per source record (e.g. one gov.il decision). A document moves through

    catalog → queued → processing → parsed → indexing → indexed
                                  ↘ no_text / failed

- catalog     known from the source site, nothing done with it yet
- queued      the admin asked to download + parse it; waiting for the local worker
- processing  claimed by the worker (downloading / parsing on the indexing machine)
- parsed      Markdown is stored here and can be reviewed; not searchable yet
- indexing    being embedded into the agent's vector store
- indexed     searchable in chat
- no_text     the PDF has no text layer (scan); failed = see `error`

The parsed Markdown is kept (gzip) so a document can be re-chunked or moved to
another vector store without parsing it again.
"""
from __future__ import annotations

import gzip
import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from config import DATA_DIR
from src.govil_client import GovilClient, record_key

_DB_PATH = DATA_DIR / "admin.db"
_lock = threading.Lock()

STATUSES = ("catalog", "queued", "processing", "parsed", "indexing", "indexed", "no_text", "failed")
STALE_CLAIM_MINUTES = 30
MAX_BULK = 5000
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_LIST_COLUMNS = (
    "agent_id, record_key, title, meta, sort_date, status, auto_index, pages, tables, "
    "flagged_tables, chars, chunks, embed_tokens, index_cost_usd, error, updated_at, parsed_at, indexed_at"
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
            CREATE TABLE IF NOT EXISTS documents (
                agent_id       TEXT NOT NULL,
                record_key     TEXT NOT NULL,
                title          TEXT NOT NULL,
                meta           TEXT NOT NULL,
                files          TEXT NOT NULL,
                sort_date      TEXT,
                status         TEXT NOT NULL DEFAULT 'catalog',
                auto_index     INTEGER NOT NULL DEFAULT 0,
                pages          INTEGER,
                tables         INTEGER,
                flagged_tables INTEGER,
                chars          INTEGER,
                chunks         INTEGER,
                problems       TEXT,
                parsed         BLOB,
                error          TEXT,
                updated_at     TEXT NOT NULL,
                parsed_at      TEXT,
                indexed_at     TEXT,
                PRIMARY KEY (agent_id, record_key)
            );
            CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(agent_id, status);
            CREATE INDEX IF NOT EXISTS idx_documents_date ON documents(agent_id, sort_date DESC);
            CREATE TABLE IF NOT EXISTS worker_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
    _migrate()


def _migrate() -> None:
    """Columns added after the first release; ALTER TABLE keeps existing rows."""
    with _connect() as conn:
        have = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
        for column, ddl in (("embed_tokens", "INTEGER"), ("index_cost_usd", "REAL")):
            if column not in have:
                conn.execute(f"ALTER TABLE documents ADD COLUMN {column} {ddl}")


_init_db()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(row: sqlite3.Row) -> dict:
    out = dict(row)
    out["meta"] = json.loads(out["meta"])
    out["auto_index"] = bool(out["auto_index"])
    return out


# ── catalog ──────────────────────────────────────────────────────────────

def _title_and_date(agent: dict, data: dict, key: str) -> tuple[str, str | None]:
    fields = agent.get("filters", [])
    header = next((f["name"] for f in fields if "Header" in f["name"]), None)
    date_field = next((f["name"] for f in fields if f["type"] == "date"), None)
    title = str(data.get(header) or key) if header else key
    date = str(data.get(date_field) or "")[:10] or None if date_field else None
    return title, date


def upsert_catalog(agent: dict, records: list[dict]) -> dict:
    """Add source records that aren't known yet. Existing rows are left untouched."""
    schema = {f["name"] for f in agent.get("filters", [])}
    added = 0
    now = _now()
    with _lock, _connect() as conn:
        for data in records:
            key = record_key(data)
            files = [
                {"url": d["FileName"], "name": d.get("DisplayName") or "",
                 "ext": str(d.get("Extension") or "pdf").lower()}
                for d in GovilClient.document_urls(data)
            ]
            title, date = _title_and_date(agent, data, key)
            meta = {k: v for k, v in data.items() if k in schema and v not in (None, "")}
            cur = conn.execute(
                "INSERT OR IGNORE INTO documents (agent_id, record_key, title, meta, files, "
                "sort_date, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (agent["id"], key, title, json.dumps(meta, ensure_ascii=False),
                 json.dumps(files, ensure_ascii=False), date, now),
            )
            added += cur.rowcount
        conn.commit()
    return {"received": len(records), "added": added}


def known_keys(agent_id: str, keys: list[str]) -> set[str]:
    if not keys:
        return set()
    with _lock, _connect() as conn:
        marks = ",".join("?" * len(keys))
        rows = conn.execute(
            f"SELECT record_key FROM documents WHERE agent_id = ? AND record_key IN ({marks})",
            (agent_id, *keys),
        ).fetchall()
    return {r[0] for r in rows}


# ── search ───────────────────────────────────────────────────────────────

def _where(agent: dict, status: str | None, q: str | None, filters: dict | None) -> tuple[str, list]:
    clauses, params = ["agent_id = ?"], [agent["id"]]
    if status:
        if status not in STATUSES:
            raise ValueError(f"bad status: {status}")
        clauses.append("status = ?")
        params.append(status)
    if q and q.strip():
        clauses.append("title LIKE ?")
        params.append(f"%{q.strip()}%")
    schema = {f["name"]: f for f in agent.get("filters", [])}
    for name, value in (filters or {}).items():
        field = schema.get(name)
        if field is None or not _FIELD_RE.match(name) or value in (None, "", {}):
            continue
        path = f"$.{name}"
        if field["type"] == "date":
            if not isinstance(value, dict):
                continue
            if value.get("from"):
                clauses.append("substr(json_extract(meta, ?), 1, 10) >= ?")
                params += [path, str(value["from"])[:10]]
            if value.get("to"):
                clauses.append("substr(json_extract(meta, ?), 1, 10) <= ?")
                params += [path, str(value["to"])[:10]]
        elif field["type"] == "choice" or name in ("Block", "Plot"):
            clauses.append("json_extract(meta, ?) = ?")
            params += [path, str(value).strip()]
        else:
            clauses.append("json_extract(meta, ?) LIKE ?")
            params += [path, f"%{str(value).strip()}%"]
    return " AND ".join(clauses), params


def search(agent: dict, status: str | None = None, q: str | None = None,
           filters: dict | None = None, page: int = 1, page_size: int = 50) -> dict:
    where, params = _where(agent, status, q, filters)
    page, page_size = max(1, page), max(1, min(page_size, 200))
    with _lock, _connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM documents WHERE {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT {_LIST_COLUMNS} FROM documents WHERE {where} "
            "ORDER BY sort_date DESC, record_key LIMIT ? OFFSET ?",
            (*params, page_size, (page - 1) * page_size),
        ).fetchall()
    return {"total": total, "page": page, "page_size": page_size, "documents": [_row(r) for r in rows]}


def status_counts(agent_id: str) -> dict:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n, COALESCE(SUM(chunks), 0) AS chunks, "
            "COALESCE(SUM(flagged_tables), 0) AS flagged, COUNT(index_cost_usd) AS costed, "
            "COALESCE(SUM(index_cost_usd), 0) AS cost, COALESCE(SUM(embed_tokens), 0) AS tokens "
            "FROM documents WHERE agent_id = ? GROUP BY status",
            (agent_id,),
        ).fetchall()
    counts = {s: 0 for s in STATUSES}
    chunks = flagged = costed = tokens = 0
    cost = 0.0
    for r in rows:
        counts[r["status"]] = r["n"]
        flagged += r["flagged"]
        if r["status"] == "indexed":
            chunks += r["chunks"]
            costed += r["costed"]
            cost += r["cost"]
            tokens += r["tokens"]
    total = sum(counts.values())
    avg = cost / costed if costed else 0.0
    return {"counts": counts, "catalog_total": total, "indexed_chunks": chunks,
            "flagged_tables": flagged,
            "index_cost": {"total_usd": cost, "tokens": tokens, "documents": costed,
                           "avg_per_document_usd": avg,
                           "projected_catalog_usd": avg * total}}


def get_document(agent_id: str, key: str, with_parsed: bool = True) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE agent_id = ? AND record_key = ?", (agent_id, key)
        ).fetchone()
    if row is None:
        return None
    out = _row(row)
    out["files"] = json.loads(out["files"])
    out["problems"] = json.loads(out["problems"]) if out["problems"] else []
    blob = out.pop("parsed")
    if with_parsed:
        out["parsed_pages"] = json.loads(gzip.decompress(blob)) if blob else []
    return out


# ── bulk transitions ─────────────────────────────────────────────────────

def _select_keys(conn, agent: dict, keys: list[str] | None, selection: dict | None,
                 from_statuses: tuple[str, ...]) -> list[str]:
    """Resolve an explicit key list or a "everything matching these filters" selection,
    restricted to rows currently in one of `from_statuses`."""
    marks = ",".join("?" * len(from_statuses))
    if keys:
        key_marks = ",".join("?" * len(keys))
        rows = conn.execute(
            f"SELECT record_key FROM documents WHERE agent_id = ? AND status IN ({marks}) "
            f"AND record_key IN ({key_marks})",
            (agent["id"], *from_statuses, *keys[:MAX_BULK]),
        ).fetchall()
    else:
        sel = selection or {}
        where, params = _where(agent, sel.get("status"), sel.get("q"), sel.get("filters"))
        limit = max(1, min(int(sel.get("limit") or MAX_BULK), MAX_BULK))
        rows = conn.execute(
            f"SELECT record_key FROM documents WHERE {where} AND status IN ({marks}) "
            "ORDER BY sort_date DESC, record_key LIMIT ?",
            (*params, *from_statuses, limit),
        ).fetchall()
    return [r[0] for r in rows]


def transition(agent: dict, to_status: str, from_statuses: tuple[str, ...],
               keys: list[str] | None = None, selection: dict | None = None,
               auto_index: bool | None = None) -> list[str]:
    """Move the selected documents to `to_status`. Returns the keys that moved."""
    now = _now()
    with _lock, _connect() as conn:
        selected = _select_keys(conn, agent, keys, selection, from_statuses)
        for key in selected:
            if auto_index is None:
                conn.execute(
                    "UPDATE documents SET status = ?, error = NULL, updated_at = ? "
                    "WHERE agent_id = ? AND record_key = ?",
                    (to_status, now, agent["id"], key),
                )
            else:
                conn.execute(
                    "UPDATE documents SET status = ?, auto_index = ?, error = NULL, updated_at = ? "
                    "WHERE agent_id = ? AND record_key = ?",
                    (to_status, int(auto_index), now, agent["id"], key),
                )
        conn.commit()
    return selected


def set_status(agent_id: str, key: str, status: str, error: str | None = None,
               chunks: int | None = None, embed_tokens: int | None = None,
               index_cost_usd: float | None = None) -> None:
    now = _now()
    sets, params = ["status = ?", "error = ?", "updated_at = ?"], [status, error, now]
    for column, value in (("chunks", chunks), ("embed_tokens", embed_tokens),
                          ("index_cost_usd", index_cost_usd)):
        if value is not None:
            sets.append(f"{column} = ?")
            params.append(value)
    if status == "indexed":
        sets.append("indexed_at = ?")
        params.append(now)
    with _lock, _connect() as conn:
        conn.execute(
            f"UPDATE documents SET {', '.join(sets)} WHERE agent_id = ? AND record_key = ?",
            (*params, agent_id, key),
        )
        conn.commit()


# ── worker side ──────────────────────────────────────────────────────────

def claim(agent_id: str, limit: int) -> list[dict]:
    """Hand the next queued documents to the worker. Claims the worker never
    reported back on return to the queue after STALE_CLAIM_MINUTES."""
    now = _now()
    stale = (datetime.now(timezone.utc) - timedelta(minutes=STALE_CLAIM_MINUTES)).isoformat()
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE documents SET status = 'queued' WHERE agent_id = ? AND status = 'processing' "
            "AND updated_at < ?",
            (agent_id, stale),
        )
        rows = conn.execute(
            "SELECT record_key, title, files FROM documents WHERE agent_id = ? AND status = 'queued' "
            "ORDER BY updated_at, record_key LIMIT ?",
            (agent_id, max(1, min(limit, 20))),
        ).fetchall()
        for r in rows:
            conn.execute(
                "UPDATE documents SET status = 'processing', updated_at = ? "
                "WHERE agent_id = ? AND record_key = ?",
                (now, agent_id, r["record_key"]),
            )
        conn.commit()
    return [{"record_key": r["record_key"], "title": r["title"], "files": json.loads(r["files"])}
            for r in rows]


def save_parsed(agent_id: str, key: str, pages: list[dict], tables: int,
                problems: list[dict]) -> dict | None:
    """Store the worker's parse result. Returns the row (without the Markdown)."""
    chars = sum(len(p.get("markdown", "")) for p in pages)
    blob = gzip.compress(json.dumps(pages, ensure_ascii=False).encode("utf-8"))
    now = _now()
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE documents SET status = 'parsed', pages = ?, tables = ?, flagged_tables = ?, "
            "chars = ?, chunks = NULL, problems = ?, parsed = ?, error = NULL, updated_at = ?, "
            "parsed_at = ?, indexed_at = NULL WHERE agent_id = ? AND record_key = ?",
            (len(pages), tables, len(problems), chars, json.dumps(problems, ensure_ascii=False),
             blob, now, now, agent_id, key),
        )
        conn.commit()
    return get_document(agent_id, key, with_parsed=False)


def heartbeat(info: dict | None = None) -> None:
    value = json.dumps({"last_seen": _now(), **(info or {})}, ensure_ascii=False)
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO worker_state (key, value) VALUES ('worker', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (value,),
        )
        conn.commit()


def worker_status() -> dict:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT value FROM worker_state WHERE key = 'worker'").fetchone()
    if row is None:
        return {"last_seen": None, "online": False}
    info = json.loads(row[0])
    seen = datetime.fromisoformat(info["last_seen"])
    info["online"] = (datetime.now(timezone.utc) - seen) < timedelta(minutes=2)
    return info
