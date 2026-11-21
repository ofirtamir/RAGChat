"""Filter values the analyzer model finds in the question — in the call it already
makes (src/query_analyzer), so no extra model call per question.

The model gets every allowed value of the agent's choice fields and returns
the ones the question names, copied from the list. The code trusts nothing it
returns: a choice value must be a known value of that field (after Hebrew
normalisation), numbers must be digits, dates must be real dates. Anything else
is dropped. A value the model marks as not explicit, or that only matches the
list with one typo, becomes a ranking preference, never a hard filter.

Party names are not filters: BM25 finds a rare name by itself (decision 2026-11-21).
"""
from __future__ import annotations

import re
from datetime import date, datetime

from src import filter_values
from src.filter_catalog import Context
from src.filter_conditions import Condition
from src.hebrew_match import normalize

MAX_ITEMS = 30
SHORT_LIST = 5          # choice fields this short (type, role, version) take explicit values only
_DIGITS = re.compile(r"\d{1,7}")


def _number_fields(agent: dict) -> list[dict]:
    """Free-text fields that hold numbers (Block, Plot); the title field is not one."""
    return [f for f in agent.get("filters", [])
            if f.get("type") == "text" and not f["name"].endswith("Header")]


def choice_values(agent: dict, ctx: Context | None) -> dict[str, list[str]]:
    """{field: the values the model may pick}: one label per entity (duplicates
    merged), without values that name the whole collection ("שמאי מכריע")."""
    out = {}
    for f in agent.get("filters", []):
        if f.get("type") != "choice":
            continue
        name = f["name"]
        if ctx is not None:
            vals = {e.label for (n, k), e in ctx.vocab.entities.items()
                    if n == name and (n, k) not in ctx.vocab.generic}
        else:
            vocab = filter_values.build({"filters": [f], "name": agent.get("name", ""),
                                         "description": agent.get("description", "")})
            vals = {e.label for (n, k), e in vocab.entities.items() if (n, k) not in vocab.generic}
        if vals:
            out[name] = sorted(vals)
    return out


def prompt_section(agent: dict, ctx: Context | None, today: date | None = None) -> str:
    """The analyzer's extra instructions (sent as their own system message, so a
    prompt edited in Langfuse still gets them)."""
    today = today or date.today()
    lines = []
    choices, numbers = choice_values(agent, ctx), {f["name"] for f in _number_fields(agent)}
    for f in agent.get("filters", []):
        name, label = f["name"], f.get("label", f["name"])
        if choices.get(name):
            lines.append(f"- {name} ({label}). One of: " + " | ".join(choices[name]))
        elif name in numbers:
            lines.append(f"- {name} ({label}). Digits only.")
        elif f.get("type") == "date":
            lines.append(f"- {name} ({label}). A date range: \"from\" and/or \"to\" as YYYY-MM-DD.")
    if not lines:
        return ""
    fields = "\n".join(lines)
    return f"""## TASK 3: FILTERS — catalog values the question names

Only when needs_retrieval is true; otherwise "filters": []. Add the key "filters" to the JSON:
a list of items, each {{"field": "<name>", "value": "<value>", "explicit": true|false}},
or for a date field {{"field": "<name>", "from": "YYYY-MM-DD", "to": "YYYY-MM-DD", "explicit": true|false}}.

Fields:
{fields}

Rules:
1. Copy a listed value exactly as it is written in the list. Never return a value that is not in the list.
   The question may write it with a prefix letter (ב/ל/מ/ה/ו/ש), another spelling, or the given name first.
2. A place is the local committee when the question is about decisions, properties or cases in that place
   ("היטל השבחה בתל אביב", "בוועדה המקומית רמת גן"). A word that is only an everyday word or part of a
   street name ("באזור התעשייה", "ברחובות העיר", "רחוב הרצל") is not a committee.
3. An appraiser counts when the question names them. A family name several listed appraisers share:
   return each of them, explicit true only when "השמאי"/"שמאי" (or similar) stands before the name.
4. The appraisal type, appraiser role or version only when the question asks about that kind of decision
   ("תביעות פיצויים", "שמאי מייעץ", "שומה מתוקנת אחרי ערר"), not just because the topic is mentioned.
5. Block (גוש) and plot (חלקה) only when the question gives their numbers with those words. A lot
   in a plan ("מגרש 40A"), a plan number ("תמ\"ל 3003") or a section is not a block or a plot. Several numbers: one item each.
6. Dates are decision dates unless the question speaks of publication ("פורסם", "פרסום"). Today is
   {today.isoformat()}. "ב-2022" → from 2022-01-01 to 2022-12-31; "מ-2022" / "מאז 2020" → from only;
   "לפני 2019" → to 2018-12-31; "בשנה שעברה" → the whole previous calendar year
   ({today.year - 1}-01-01 to {today.year - 1}-12-31); "בשנתיים האחרונות" → from the same day two years ago.
   A date the question states is explicit.
7. explicit is true when the question clearly asks for that value. explicit false is only for the
   doubtful cases above (a family name without "שמאי", a place that may not be the committee).
   Never return a field the question does not mention.
8. A follow-up that refers to an earlier turn ("באותה ועדה", "ומה לגבי 2021") carries the earlier values over.
9. Nothing found → "filters": []."""


# ── the model's answer → conditions ──────────────────────────────────────

def _day(value, end: bool = False) -> int | None:
    text = str(value or "").strip()
    try:
        if re.fullmatch(r"\d{4}", text):
            d = date(int(text), 12, 31) if end else date(int(text), 1, 1)
        else:
            d = datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return d.year * 10000 + d.month * 100 + d.day if 1990 <= d.year <= date.today().year + 1 else None


def range_display(start: int | None, end: int | None) -> str:
    def full(d: int) -> str:
        return f"{d % 100:02d}.{d // 100 % 100:02d}.{d // 10000}"
    if start and end and start % 10000 == 101 and end % 10000 == 1231:
        a, b = start // 10000, end // 10000
        return str(a) if a == b else f"{a}–{b}"
    if start and end:
        return f"{full(start)}–{full(end)}"
    if start:
        return f"מ-{start // 10000}" if start % 10000 == 101 else f"מ-{full(start)}"
    return f"עד {end // 10000}" if end % 10000 == 1231 else f"עד {full(end)}"


def _entity(ctx: Context, name: str, value: str) -> tuple[filter_values.Entity | None, bool]:
    """The known entity a returned value names, and whether the match is exact."""
    ent = ctx.vocab.entity_for(name, value)
    if ent is not None:
        return ent, True
    for text in ctx.vocab.fuzzy.lookup(normalize(value)):
        hits = {p.entity.key: p.entity for p in ctx.vocab.by_text.get(text, [])
                if p.entity.field == name and p.kind == "full"}
        if len(hits) == 1:
            return next(iter(hits.values())), False
    return None, False


def _merge(target: dict[str, Condition], c: Condition) -> None:
    old = target.get(c.field)
    if old is None:
        target[c.field] = c
    elif c.kind != "range":
        old.values = sorted(set(old.values) | set(c.values))
        if c.display not in old.display.split(", "):
            old.display += f", {c.display}"
        old.matched = f"{old.matched} {c.matched}".strip()


def parse(agent: dict, ctx: Context, items) -> tuple[list[Condition], list[Condition]]:
    """(hard, soft) conditions from the model's "filters" list; invalid items are skipped."""
    schema = {f["name"]: f for f in agent.get("filters", [])}
    numeric = {f["name"] for f in _number_fields(agent)}
    hard: dict[str, Condition] = {}
    soft: dict[str, Condition] = {}
    for item in (items if isinstance(items, list) else [])[:MAX_ITEMS]:
        if not isinstance(item, dict) or item.get("field") not in schema:
            continue
        name = item["field"]
        f = schema[name]
        label = f.get("label", name)
        explicit = item.get("explicit", True) is not False
        raw = str(item.get("value") or "").strip()[:120]
        if f.get("type") == "choice":
            ent, exact = _entity(ctx, name, raw)
            if ent is None or (name, ent.key) in ctx.vocab.generic:
                continue
            if not explicit and len(f.get("values") or []) <= SHORT_LIST:
                continue   # a guess at a role/type/version (the model offers "שמאי מייעץ" for any "השמאי")
            c = Condition(name, "in", ent.values(), label=label, display=ent.label, matched=raw,
                          certain=explicit and exact)
        elif name in numeric:
            nums = list(dict.fromkeys(_DIGITS.findall(raw)))
            if not nums:
                continue
            c = Condition(name, "num", nums, label=label, display=", ".join(nums), matched=raw, certain=explicit)
        elif f.get("type") == "date":
            start, end = _day(item.get("from")), _day(item.get("to"), end=True)
            if start is None and end is None:
                continue
            if start and end and start > end:
                start, end = end, start
            # always a filter: the model marks "בשנתיים האחרונות" not explicit although it
            # computes the range right (check 2026-11-21); a date nobody named is never returned
            c = Condition(name, "range", [], start, end, label, range_display(start, end))
        else:
            continue
        _merge(hard if c.certain else soft, c)
    for name in hard:
        soft.pop(name, None)
    return list(hard.values()), list(soft.values())
