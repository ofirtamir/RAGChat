"""When the filters found in a question match no indexed decision: loosen them
one step at a time instead of dropping them all (the user's own panel choices
are never loosened).

1. Widen every date range by a year on each side.
2. Drop as few found filters as possible, least telling first (version, role,
   dates, plot, appraiser, block, type, committee), until something matches.

All of it is counting in the catalog snapshot — no index search, no model call.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from itertools import combinations

from src.filter_catalog import Context
from src.filter_conditions import KEYS_FIELD, Condition

DROP_ORDER = (KEYS_FIELD, "AppraisalVersion", "AppraiserType", "PublicityDate", "DecisionDate", "Plot",
              "DecisiveAppraiser", "Block", "AppraisalType", "Committee")
MAX_TRIES = 200


@dataclass
class Relaxed:
    kept: list[Condition]
    keys: list[str]
    dropped: list[Condition] = field(default_factory=list)
    widened: list[tuple[Condition, Condition]] = field(default_factory=list)   # (asked, used)


def widen(c: Condition, years: int = 1) -> Condition:
    from src.model_filters import range_display
    start = c.start - years * 10000 if c.start else None
    end = c.end + years * 10000 if c.end else None
    return replace(c, start=start, end=end, display=range_display(start, end))


def _rank(c: Condition) -> int:
    return DROP_ORDER.index(c.field) if c.field in DROP_ORDER else len(DROP_ORDER)


def relax(ctx: Context, manual: list[Condition], found: list[Condition]) -> Relaxed | None:
    """The loosest change of `found` that matches some decision; None when only
    dropping every found filter would."""
    if any(c.kind == "range" for c in found):
        wide = [widen(c) if c.kind == "range" else c for c in found]
        if keys := ctx.matching(manual + wide):
            return Relaxed(wide, keys, widened=[(a, b) for a, b in zip(found, wide) if a is not b])
    order = sorted(range(len(found)), key=lambda i: _rank(found[i]))
    tries = 0
    for n in range(1, len(found)):
        for drop in combinations(order, n):
            tries += 1
            if tries > MAX_TRIES:
                return None
            kept = [c for i, c in enumerate(found) if i not in drop]
            if keys := ctx.matching(manual + kept):
                return Relaxed(kept, keys, dropped=[found[i] for i in sorted(drop)])
    return None
