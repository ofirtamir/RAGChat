"""One filter condition, usable three ways: as a LanceDB SQL clause, as a test
on a catalog record (to count matches without touching the index), and as a
small dict the chat UI shows and sends back on the next turn.

Kinds:
- in     the field equals one of `values` (every stored spelling of one entity,
         or of several when a family name fits several appraisers)
- num    Block/Plot: the field holds one of the numbers in `values`. These fields
         may hold several numbers ("81,84,90"), so equality would miss them.
- range  a date field between `start` and `end` (YYYYMMDD, either may be None)
- keys   specific decisions (record keys), e.g. a party name found in titles
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

KINDS = ("in", "num", "range", "keys")
KEYS_FIELD = "_decision"
_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_MAX_VALUES = 500


def _sql_str(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def date_int(value) -> int | None:
    digits = re.sub(r"\D", "", str(value or "")[:10])
    return int(digits) if len(digits) == 8 else None


@dataclass
class Condition:
    field: str
    kind: str
    values: list[str] = field(default_factory=list)
    start: int | None = None
    end: int | None = None
    label: str = ""                # the field's Hebrew name
    display: str = ""              # what the chip shows ("ירושלים", "2025")
    matched: str = ""              # the words in the question it came from
    source: str = "auto"           # auto | manual | inherited
    certain: bool = True

    def to_sql(self) -> str:
        if self.kind == "in":
            return f"{self.field} IN ({', '.join(_sql_str(v) for v in self.values)})"
        if self.kind == "num":
            parts = [f"regexp_like({self.field}, '(^|[^0-9]){n}([^0-9]|$)')" for n in self.values]
            return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"
        if self.kind == "keys":
            return f"record_key IN ({', '.join(_sql_str(v) for v in self.values)})"
        parts = []
        if self.start is not None:
            parts.append(f"{self.field}_int >= {int(self.start)}")
        if self.end is not None:
            parts.append(f"{self.field}_int <= {int(self.end)}")
        return " AND ".join(parts)

    def test(self, meta: dict, key: str = "") -> bool:
        """Does a catalog record (its metadata and key) satisfy the condition?"""
        if self.kind == "keys":
            return key in self.values
        raw = meta.get(self.field)
        if self.kind == "in":
            return str(raw or "").strip() in self.values
        if self.kind == "num":
            text = str(raw or "")
            return any(re.search(rf"(^|[^0-9]){n}([^0-9]|$)", text) for n in self.values)
        d = date_int(raw)
        if d is None:
            return False
        return (self.start is None or d >= self.start) and (self.end is None or d <= self.end)

    def to_dict(self) -> dict:
        return asdict(self)


def where_sql(conditions: list[Condition]) -> str | None:
    # every clause is a single predicate or already parenthesised (num with several numbers)
    return " AND ".join(p for p in (c.to_sql() for c in conditions) if p) or None


def any_sql(conditions: list[Condition]) -> str | None:
    """Rows matching at least one condition (the "preferred" ranking boost)."""
    parts = [c.to_sql() for c in conditions]
    parts = [f"({p})" for p in parts if p]
    return " OR ".join(parts) or None


def from_dict(agent: dict, data: dict) -> Condition | None:
    """A condition sent back by the client (an inherited filter), validated:
    known field and kind, plain strings, digits for numbers, safe record keys."""
    if not isinstance(data, dict):
        return None
    names = {f["name"]: f for f in agent.get("filters", [])}
    name, kind = data.get("field"), data.get("kind")
    if kind not in KINDS or (name not in names and not (kind == "keys" and name == KEYS_FIELD)):
        return None
    values = data.get("values") or []
    if not isinstance(values, list) or len(values) > _MAX_VALUES:
        return None
    values = [str(v)[:200] for v in values if isinstance(v, (str, int))]
    if kind == "num" and not all(v.isdigit() and len(v) <= 7 for v in values):
        return None
    if kind == "keys" and not all(_KEY_RE.match(v) for v in values):
        return None
    start, end = data.get("start"), data.get("end")
    if kind == "range":
        if names[name].get("type") != "date":
            return None
        if any(x is not None and (not isinstance(x, int) or not 10000101 <= x <= 99991231) for x in (start, end)):
            return None
        if start is None and end is None:
            return None
    elif not values:
        return None
    text = lambda k: str(data.get(k) or "")[:120]  # noqa: E731
    return Condition(name, kind, values, start if kind == "range" else None, end if kind == "range" else None,
                     label=text("label"), display=text("display"), matched=text("matched"),
                     source="inherited", certain=True)


def from_manual(agent: dict, filters: dict | None, expand=None) -> list[Condition]:
    """The filters the user picked in the UI. `expand(field, value)` returns every
    stored spelling of a choice value (see filter_values.stored_forms)."""
    out = []
    schema = {f["name"]: f for f in agent.get("filters", [])}
    for name, value in (filters or {}).items():
        f = schema.get(name)
        if f is None or value in (None, "", {}) or name.endswith("Header"):
            continue
        label = f.get("label") or name
        if f["type"] == "date":
            if not isinstance(value, dict):
                continue
            start, end = date_int(value.get("from")), date_int(value.get("to"))
            if start is None and end is None:
                continue
            shown = " – ".join(str(value.get(k) or "") for k in ("from", "to"))
            out.append(Condition(name, "range", [], start, end, label, shown, source="manual"))
            continue
        value = str(value).strip()
        numbers = re.findall(r"\d+", value)
        if name in ("Block", "Plot") and numbers and len(numbers) <= 20:
            out.append(Condition(name, "num", numbers, label=label, display=value, source="manual"))
        else:
            forms = expand(name, value) if expand and f["type"] == "choice" else [value]
            out.append(Condition(name, "in", forms, label=label, display=value, source="manual"))
    return out
