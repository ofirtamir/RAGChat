"""An in-memory snapshot of the library catalog, for filtering by the question.

Built once per agent from admin.db (one query, ~31K small rows) and refreshed in
the background every few minutes, so a question never scans the index. It holds:

- the value dictionary (src/filter_values) — schema values + stored values
- for every *indexed* decision: its filter metadata, size and display name, plus
  {field: {value: keys}} for fast counting
- a word index of the party names in the titles ("… בעניין היטל השבחה
  <party> נ ועדה מקומית <X> ג <block> ח <plot> - <appraiser>")
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field

from src import filter_values
from src.filter_conditions import Condition
from src.hebrew_match import normalize

logger = logging.getLogger(__name__)

REFRESH_SECONDS = 600
_IN_RE = normalize("בעניין")
_TITLE_END = re.compile(r"\sג['׳]?\s*\d|\s[–—-]\s[^–—-]*$")
PARTY_STOP = frozenset(normalize(w) for w in """
    נ נגד ועדה ועדת הועדה מקומית המקומית מחוזית לתכנון תכנון ובניה בניה עירית עיריית מועצה
    אזורית בעמ בע"מ חברה חברת ואח ואחרים ואחר עמ את של ו ה ב ל מ שמאי מכריע הכרעת מיום
    היטל השבחה תביעת פיצויים תיק הפקעות הפקעה ערר עורר עוררת
""".split())


@dataclass
class Context:
    vocab: filter_values.Vocabulary
    records: dict[str, dict] = field(default_factory=dict)         # key → filter metadata
    sizes: dict[str, int] = field(default_factory=dict)            # key → characters
    sources: dict[str, str] = field(default_factory=dict)          # key → display name
    by_value: dict[str, dict[str, set[str]]] = field(default_factory=dict)
    party: dict[str, set[str]] = field(default_factory=dict)       # title word → keys
    built_at: float = 0.0

    @property
    def has_index(self) -> bool:
        return bool(self.records)

    def matching(self, conditions: list[Condition]) -> list[str]:
        """Keys of indexed decisions satisfying every condition."""
        keys: set[str] | None = None
        rest = []
        for c in conditions:
            if c.kind == "in" and c.field in self.by_value:
                hit = set().union(*(self.by_value[c.field].get(v, set()) for v in c.values))
            elif c.kind == "keys":
                hit = set(c.values) & self.records.keys()
            else:
                rest.append(c)
                continue
            keys = hit if keys is None else keys & hit
        pool = self.records.keys() if keys is None else keys
        return sorted(k for k in pool if all(c.test(self.records[k], k) for c in rest))


def party_words(title: str) -> set[str]:
    """Normalised words of the party part of a decision title."""
    m = _TITLE_END.search(title or "")
    text = (title or "")[:m.start()] if m else (title or "")
    toks = normalize(text).split()
    if _IN_RE in toks:
        toks = toks[toks.index(_IN_RE) + 1:]
    return {t for t in toks if len(t) >= 2 and not t.isdigit() and t not in PARTY_STOP}


def build_context(agent: dict, rows) -> Context:
    """rows: (record_key, title, meta JSON, status, chars), as library_store.filter_rows."""
    names = [f["name"] for f in agent.get("filters", [])]
    choice = [f["name"] for f in agent.get("filters", []) if f.get("type") == "choice"]
    headers = [n for n in names if n.endswith("Header")]
    stored: dict[str, set[str]] = {n: set() for n in choice}
    parsed = []
    for key, title, meta_json, status, chars in rows:
        try:
            meta = json.loads(meta_json) if isinstance(meta_json, str) else dict(meta_json or {})
        except (TypeError, ValueError):
            meta = {}
        for n in choice:
            if meta.get(n) not in (None, ""):
                stored[n].add(str(meta[n]).strip())
        parsed.append((key, title, meta, status, chars))
    ctx = Context(filter_values.build(agent, stored))
    from src.agent_ingest import source_name
    for key, title, meta, status, chars in parsed:
        if status != "indexed":
            continue
        ctx.records[key] = {n: meta.get(n) for n in names if meta.get(n) not in (None, "")}
        ctx.sizes[key] = int(chars or 0)
        ctx.sources[key] = source_name(agent, meta, title or key)
        for n in headers:                       # the title filter tests what the index's `source` holds
            ctx.records[key][n] = ctx.sources[key]
        for n in choice:
            if meta.get(n) not in (None, ""):
                ctx.by_value.setdefault(n, {}).setdefault(str(meta[n]).strip(), set()).add(key)
        for w in party_words(title):
            ctx.party.setdefault(w, set()).add(key)
    ctx.built_at = time.time()
    return ctx


_contexts: dict[str, Context] = {}
_lock = threading.Lock()
_refreshing: set[str] = set()


def _load(agent: dict) -> Context:
    try:
        from src import library_store
        rows = library_store.filter_rows(agent["id"])
    except Exception:
        logger.exception("filter catalog: reading the catalog failed; using the schema values only")
        rows = []
    return build_context(agent, rows)


def _refresh(agent: dict) -> None:
    try:
        _contexts[agent["id"]] = _load(agent)
    finally:
        _refreshing.discard(agent["id"])


def get_context(agent: dict, wait: bool = True) -> Context | None:
    """The agent's snapshot: built on first use, refreshed in the background when
    old. With wait=False a missing snapshot is started in the background and None
    is returned (the agents list must not wait for it)."""
    ctx = _contexts.get(agent["id"])
    if ctx is None and not wait:
        if agent["id"] not in _refreshing:
            _refreshing.add(agent["id"])
            threading.Thread(target=_refresh, args=(agent,), daemon=True).start()
        return None
    if ctx is None:
        with _lock:
            ctx = _contexts.get(agent["id"])
            if ctx is None:
                ctx = _contexts[agent["id"]] = _load(agent)
        return ctx
    if time.time() - ctx.built_at > REFRESH_SECONDS and agent["id"] not in _refreshing:
        _refreshing.add(agent["id"])
        threading.Thread(target=_refresh, args=(agent,), daemon=True).start()
    return ctx


def mark_stale(agent_id: str) -> None:
    """The catalog changed (e.g. new values synced): refresh on the next question."""
    if ctx := _contexts.get(agent_id):
        ctx.built_at = 0.0


def set_context(agent_id: str, ctx: Context | None) -> None:
    """Install (or drop, with None) a snapshot — after a catalog sync, and in tests."""
    if ctx is None:
        _contexts.pop(agent_id, None)
    else:
        _contexts[agent_id] = ctx
