"""Decide which filters a question runs with, before any search.

1. The user's own choices from the filter panel (always win for their field).
2. Values found in the question (src/query_filters): certain ones filter, the
   rest only raise matching decisions in the ranking.
3. A follow-up ("באותה החלטה", "ומה שם?") keeps the previous turn's filters.
4. Count the matching indexed decisions in the catalog snapshot (no index scan):
     0          drop the found filters, search everything, and say so
     1–3        read those decisions whole (if they fit the size budget)
     more       filter and search as usual
Fields the user removed from the chips (`disabled`) are skipped.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src import filter_catalog, filter_values, query_filters
from src.filter_conditions import KEYS_FIELD, Condition, any_sql, from_dict, from_manual, where_sql
from src.hebrew_match import normalize, prefix_splits

logger = logging.getLogger(__name__)

FULL_READ_MAX = 3
FULL_READ_MAX_CHARS = 90_000
SOFT_MAX_SHARE = 0.3        # a preference matching more of the decisions than this is dropped
FOLLOW_UP_CUES = frozenset(normalize(w) for w in "אותה אותו אותן אותם הנל הזו הזאת הזה ההיא ההוא שם".split())


@dataclass
class FilterPlan:
    hard: list[Condition] = field(default_factory=list)
    soft: list[Condition] = field(default_factory=list)
    dropped: list[Condition] = field(default_factory=list)
    count: int | None = None           # matching indexed decisions (None: not counted)
    mode: str = "none"                 # none | filtered | full_read | relaxed
    keys: list[str] = field(default_factory=list)       # full_read: the decisions
    sources: list[str] = field(default_factory=list)    # full_read: their display names
    note: str = ""

    @property
    def where(self) -> str | None:
        return where_sql(self.hard)

    @property
    def soft_where(self) -> str | None:
        return any_sql(self.soft)

    @property
    def found_any(self) -> bool:
        return any(c.source != "manual" for c in self.hard + self.soft + self.dropped)

    def to_dict(self) -> dict:
        """For the chat UI: chips, the note, and what the next turn may inherit."""
        chip = Condition.to_dict
        inherit = [chip(c) for c in self.hard if c.source != "manual"]
        if self.mode == "full_read":
            inherit = [c for c in inherit if c["field"] != KEYS_FIELD] + [Condition(
                KEYS_FIELD, "keys", self.keys, label="החלטה", display=f"{len(self.keys)} החלטות").to_dict()]
        return {"applied": [chip(c) for c in self.hard], "preferred": [chip(c) for c in self.soft],
                "dropped": [chip(c) for c in self.dropped], "count": self.count, "mode": self.mode,
                "note": self.note, "inherit": inherit}


def is_follow_up(query: str) -> bool:
    for tok in normalize(query).split():
        if tok in FOLLOW_UP_CUES or any(rest in FOLLOW_UP_CUES for _, rest in prefix_splits(tok)):
            return True
    return False


def _narrow(ctx: filter_catalog.Context, conditions: list[Condition]) -> list[Condition]:
    """Preferences worth keeping: a value most decisions share ("היטל השבחה" is
    ~98% of them) says nothing about the question and only reshuffles the ranking
    (benchmark, 2026-11-20)."""
    if not ctx.has_index:
        return conditions
    limit = SOFT_MAX_SHARE * len(ctx.records)
    return [c for c in conditions if len(ctx.matching([c])) <= limit]


def _names(conditions: list[Condition]) -> str:
    return ", ".join(f"{c.label}: {c.display}" for c in conditions)


def plan_filters(agent: dict, query: str, filters: dict | None = None,
                 inherited: list[dict] | None = None, disabled: list[str] | None = None,
                 ctx: filter_catalog.Context | None = None) -> FilterPlan:
    ctx = ctx or filter_catalog.get_context(agent)
    disabled = set(disabled or [])
    manual = from_manual(agent, filters, lambda f, v: filter_values.stored_forms(ctx.vocab, f, v))
    taken = {c.field for c in manual} | disabled
    try:
        found = query_filters.detect(agent, query, ctx)
    except Exception:
        logger.exception("filter detection failed; searching without found filters")
        found = query_filters.Detection()
    auto = [c for c in found.hard if c.field not in taken]
    taken |= {c.field for c in auto}
    soft = _narrow(ctx, [c for c in found.soft if c.field not in taken])
    if inherited and is_follow_up(query):
        for data in inherited:
            c = from_dict(agent, data)
            if c and c.field not in taken:
                auto.append(c)
                taken.add(c.field)
    plan = FilterPlan(hard=manual + auto, soft=soft)
    if not auto:
        plan.mode = "filtered" if manual else "none"
        return plan
    if not ctx.has_index:          # no catalog snapshot (e.g. a fresh deployment): trust the filters
        plan.mode = "filtered"
        return plan
    keys = ctx.matching(plan.hard)
    plan.count = len(keys)
    if not keys:
        plan.dropped, plan.hard = auto, manual
        plan.soft = soft + _narrow(ctx, [c for c in auto if c.kind != "keys" or len(c.values) <= 1000])
        plan.count = len(ctx.matching(manual)) if manual else None
        plan.mode = "relaxed"
        plan.note = f"לא נמצאו החלטות שמתאימות ל{_names(auto)}, ולכן החיפוש נעשה בלי הסינון הזה."
        return plan
    size = sum(ctx.sizes.get(k, 0) for k in keys)
    if len(keys) <= FULL_READ_MAX and size <= FULL_READ_MAX_CHARS:
        plan.mode, plan.keys = "full_read", keys
        plan.sources = [ctx.sources.get(k, k) for k in keys]
        plan.note = f"נמצאו {len(keys)} החלטות מתאימות, והן נקראו במלואן."
    else:
        plan.mode = "filtered"
        plan.note = f"החיפוש צומצם ל-{len(keys):,} החלטות."
    return plan


def filters_node(state: dict) -> dict:
    """Graph node (src/graph.py): plan the filters once, on the user's own words.
    A handful of matching decisions are read whole (full-document mode)."""
    if not state.get("agent_id"):
        return {}
    from src.agents import get_agent
    from src.full_doc_detector import FullDocDecision

    plan = plan_filters(get_agent(state["agent_id"]), state["query"], state.get("filters"),
                        state.get("inherited_filters"), state.get("disabled_filters"))
    out: dict = {"filter_plan": plan}
    if plan.mode == "full_read":
        out["full_doc_decision"] = FullDocDecision(needs_full_document=True, target_sources=plan.sources,
                                                   reasoning=plan.note)
    return out


def ui_filter_schema(agent: dict) -> list[dict]:
    """The agent's filter schema for the chat's filter panel, with choice lists
    from the value dictionary (the schema's own lists until it is built)."""
    ctx = filter_catalog.get_context(agent, wait=False)
    return filter_values.ui_filters(agent, ctx.vocab) if ctx else agent.get("filters", [])


def step_detail(plan: FilterPlan | None) -> str:
    if plan is None or not (plan.hard or plan.soft or plan.dropped):
        return "לא זוהו מסננים"
    shown = _names(plan.hard) or _names(plan.soft)
    return f"{shown} · {plan.note}" if plan.note else shown
