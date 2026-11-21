"""Decide which filters a question runs with, before any search.

1. The user's own choices from the filter panel (always win for their field).
2. Values found in the question: by the analyzer model in the call it already
   makes (src/model_filters), with block/plot/dates it missed from the regexes;
   by the dictionary parser (src/query_filters) when the model gave no answer.
   Certain ones filter, the rest only raise matching decisions in the ranking.
3. A follow-up ("באותה החלטה", "ומה שם?") keeps the previous turn's filters.
4. Count the matching indexed decisions in the catalog snapshot (no index scan):
     0          loosen the found filters step by step (src/filter_relax) and say
                so; also say when matching decisions exist but are not indexed yet
     1–3        read those decisions whole (if they fit the size budget)
     more       filter and search as usual
Fields the user removed from the chips (`disabled`) are skipped.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace

from src import filter_catalog, filter_relax, filter_values, model_filters, query_filters
from src.filter_conditions import KEYS_FIELD, Condition, any_sql, date_int, from_dict, from_manual, where_sql
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


def _detect(agent: dict, query: str, ctx: filter_catalog.Context,
            model_items: list[dict] | None) -> query_filters.Detection:
    """The model's values, except where the regexes are exact: a block/plot
    number written with "גוש"/"חלקה" and a year or date written out come from
    the regexes (the model read "מ-2022" as only 2022 and "מגרש 40A" as a plot;
    comparison 2026-11-21). A number only the model saw is a preference."""
    try:
        if model_items is None:
            return query_filters.detect(agent, query, ctx)
        hard, soft = model_filters.parse(agent, ctx, model_items)
        numeric, _ = query_filters.numbers_and_dates(agent, query)
        exact = {c.field: c for c in numeric if c.kind == "num"}
        exact_date = next((c for c in numeric if c.kind == "range"), None)
        out = query_filters.Detection()
        for c in hard + soft:
            if c.kind == "num":
                if c.field not in exact:
                    out.soft.append(replace(c, certain=False))
                continue
            if c.kind == "range" and exact_date:
                c = replace(c, start=exact_date.start, end=exact_date.end, display=exact_date.display,
                            matched=exact_date.matched, certain=True)
                exact_date = None
            (out.hard if c.certain else out.soft).append(c)
        out.hard += list(exact.values())
        if exact_date and not any(c.kind == "range" for c in out.hard + out.soft):
            out.hard.append(exact_date)
        return out
    except Exception:
        logger.exception("filter detection failed; searching without found filters")
        return query_filters.Detection()


def _pending_note(agent: dict, conditions: list[Condition]) -> str:
    n = filter_catalog.count_unindexed(agent, conditions)
    if not n:
        return ""
    return (f" יש במאגר {n:,} החלטות שמתאימות לכל המסננים, אבל הן עוד לא נקלטו לחיפוש." if n > 1 else
            " יש במאגר החלטה אחת שמתאימה לכל המסננים, אבל היא עוד לא נקלטה לחיפוש.")


def plan_filters(agent: dict, query: str, filters: dict | None = None,
                 inherited: list[dict] | None = None, disabled: list[str] | None = None,
                 ctx: filter_catalog.Context | None = None,
                 model_items: list[dict] | None = None) -> FilterPlan:
    """`model_items`: the analyzer's "filters" answer (None → the dictionary parser)."""
    ctx = ctx or filter_catalog.get_context(agent)
    disabled = set(disabled or [])
    manual = from_manual(agent, filters, lambda f, v: filter_values.stored_forms(ctx.vocab, f, v))
    taken = {c.field for c in manual} | disabled
    found = _detect(agent, query, ctx, model_items)
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
    if not ctx.has_index:          # no catalog snapshot (e.g. a fresh deployment): trust the filters
        plan.mode = "filtered" if plan.hard else "none"
        return plan
    if not auto:
        plan.mode = "filtered" if manual else "none"
        if manual and not ctx.matching(manual):
            plan.count = 0
            plan.note = "לא נמצאו החלטות שמתאימות למסננים שבחרת." + _pending_note(agent, manual)
        return plan
    keys = ctx.matching(plan.hard)
    plan.count = len(keys)
    note = ""
    if not keys:
        pending = _pending_note(agent, plan.hard)
        loose = filter_relax.relax(ctx, manual, auto)
        if loose is None:
            plan.dropped, plan.hard = auto, manual
            plan.soft = soft + _narrow(ctx, [c for c in auto if c.kind != "keys" or len(c.values) <= 1000])
            plan.count = len(ctx.matching(manual)) if manual else None
            plan.mode = "relaxed"
            plan.note = f"לא נמצאו החלטות שמתאימות ל{_names(auto)}, ולכן החיפוש נעשה בלי הסינון הזה." + pending
            return plan
        keys = loose.keys
        plan.hard, plan.dropped, plan.count = manual + loose.kept, loose.dropped, len(keys)
        plan.soft = soft + _narrow(ctx, [c for c in loose.dropped if c.kind != "keys"])
        if loose.widened:
            asked = ", ".join(a.display for a, _ in loose.widened)
            used = ", ".join(b.display for _, b in loose.widened)
            note = f"לא נמצאו החלטות ב-{asked}, ולכן טווח התאריכים הורחב ל-{used}."
        else:
            note = f"לא נמצאו החלטות שמתאימות לכל המסננים, ולכן הוסר הסינון לפי {_names(loose.dropped)}."
        note += pending
    size = sum(ctx.sizes.get(k, 0) for k in keys)
    if len(keys) <= FULL_READ_MAX and size <= FULL_READ_MAX_CHARS:
        plan.mode, plan.keys = "full_read", keys
        plan.sources = [ctx.sources.get(k, k) for k in keys]
        plan.note = f"{note} נמצאו {len(keys)} החלטות מתאימות, והן נקראו במלואן.".strip()
    else:
        plan.mode = "filtered"
        plan.note = f"{note} החיפוש צומצם ל-{len(keys):,} החלטות.".strip()
    return plan


def filters_node(state: dict) -> dict:
    """Graph node (src/graph.py): plan the filters once, on the user's own words.
    A handful of matching decisions are read whole (full-document mode)."""
    if not state.get("agent_id"):
        return {}
    from src.agents import get_agent
    from src.full_doc_detector import FullDocDecision

    plan = plan_filters(get_agent(state["agent_id"]), state["query"], state.get("filters"),
                        state.get("inherited_filters"), state.get("disabled_filters"),
                        model_items=state.get("model_filters"))
    out: dict = {"filter_plan": plan}
    if plan.mode == "full_read":
        out["full_doc_decision"] = FullDocDecision(needs_full_document=True, target_sources=plan.sources,
                                                   reasoning=plan.note)
    return out


def matching_documents(agent: dict, filters: dict | None, limit: int = 0) -> dict:
    """How many indexed decisions the filter panel's choices leave and, with
    limit > 0, the newest of them — shown under the panel. Catalog only, free."""
    ctx = filter_catalog.get_context(agent)
    manual = from_manual(agent, filters, lambda f, v: filter_values.stored_forms(ctx.vocab, f, v))
    keys = ctx.matching(manual)
    out = {"count": len(keys), "total": len(ctx.records), "documents": []}
    if limit <= 0 or not keys:
        return out
    dates = [f["name"] for f in agent.get("filters", []) if f.get("type") == "date"][::-1]
    stamp = lambda k: tuple(date_int(ctx.records[k].get(d)) or 0 for d in dates)  # noqa: E731
    for key in sorted(keys, key=stamp, reverse=True)[:limit]:
        day = next((x for x in stamp(key) if x), None)
        out["documents"].append({"key": key, "title": ctx.sources.get(key, key),
                                 "date": f"{day % 100:02d}.{day // 100 % 100:02d}.{day // 10000}" if day else None})
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
