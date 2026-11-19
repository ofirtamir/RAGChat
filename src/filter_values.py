"""The value dictionary for the agent's choice filters (committee, appraiser…).

Built once per agent from one source: the `values` lists in the agent's schema
file (the same lists the chat's dropdowns show), joined with the distinct values
stored in the library catalog. A value that appears in newly synced decisions is
therefore picked up on the next catalog refresh, with no list to edit by hand.

Stored spellings of the same value ("פז יעקב (ג'קי)" / "פז יעקב –ג'קי") become
one entity; a filter on it matches every stored form.

Each entity gets the phrases a question may use for it (normalised token tuples):
- committee:  the name; without a leading article ("הגלבוע" → "גלבוע"); the
  first part of a dashed name ("תל אביב-יפו" → "תל אביב"); a unique leading
  part of a long name ("פרדס חנה כרכור" → "פרדס חנה")
- appraiser:  stored as "family given": also "given family", the nickname
  ("ג'קי פז"), and the family name alone (kind "surname")
- other fields: the value, and its distinctive single words (kind "partial")
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.hebrew_match import FuzzyIndex, normalize

# Committee names that are also everyday words: alone they are not proof that
# the question means the committee ("באזור", "בשדרות רוטשילד"). Measured on the
# decisions on 2026-11-19 (e.g. "אזור" appears in nearly every decision).
AMBIGUOUS_WORDS = frozenset(normalize(w) for w in """
    אזור עומר שדרות גזר תמר קסם רחובות גולן הראל יואב קריות זמורה שפיר משגב שקמים
""".split())
_NICK = re.compile(r"\(([^)]*)\)|\s[–—-]\s*([^–—-]+)$")
_MIN_PARTIAL = 4
_GENERIC_PARTIALS = frozenset(normalize(w) for w in "שומה שמאי תיק תביעת".split())


@dataclass
class Entity:
    field: str
    key: str                       # normalised identity
    label: str                     # display form (the schema's spelling when it has one)
    stored: set[str] = field(default_factory=set)   # every stored spelling

    def values(self) -> list[str]:
        return sorted(self.stored)


@dataclass
class Phrase:
    tokens: tuple[str, ...]
    entity: Entity
    kind: str                      # full | surname | partial | article


@dataclass
class Vocabulary:
    fields: dict[str, str]                         # field name → Hebrew label
    entities: dict[tuple[str, str], Entity]
    phrases: dict[str, list[Phrase]]               # first token → phrases, longest first
    words: set[str]                                # every token that starts or is inside a phrase
    fuzzy: FuzzyIndex
    by_text: dict[str, list[Phrase]]               # joined phrase text → phrases
    generic: set[tuple[str, str]]                  # (field, key) never used as a filter

    def entity_for(self, field_name: str, stored_value: str) -> Entity | None:
        return self.entities.get((field_name, _identity(field_name, stored_value)))

    def surname_entities(self, phrase: Phrase) -> list[Entity]:
        """Every appraiser sharing this family name."""
        return [p.entity for p in self.phrases.get(phrase.tokens[0], [])
                if p.kind == "surname" and p.tokens == phrase.tokens]


def _identity(field_name: str, value: str) -> str:
    return normalize(value)


def _appraiser_parts(value: str) -> tuple[list[str], list[str], list[str]]:
    """(family tokens, given tokens, nickname tokens) of "family given (nick)"."""
    nick = []
    m = _NICK.search(value)
    base = value
    if m:
        nick = normalize(m.group(1) or m.group(2) or "").split()
        base = value[:m.start()]
    toks = normalize(base).split()
    if len(toks) < 2:
        return toks, [], nick
    return toks[:-1], toks[-1:], nick


def _entity_phrases(ent: Entity, field_name: str) -> list[tuple[tuple[str, ...], str]]:
    out: list[tuple[tuple[str, ...], str]] = []
    for raw in ent.stored | {ent.label}:
        toks = tuple(normalize(raw).split())
        if not toks:
            continue
        out.append((toks, "full"))
        if field_name == "DecisiveAppraiser":
            fam, given, nick = _appraiser_parts(raw)
            if fam and given:
                out += [(tuple(given + fam), "full"), (tuple(fam + given), "full"),
                        (tuple(fam), "surname")]
            for n in ([nick] if nick else []):
                out += [(tuple(n + fam), "full"), (tuple(fam + n), "full")]
        elif field_name == "Committee":
            if toks[0].startswith("ה") and len(toks[0]) > 3:
                out.append(((toks[0][1:],) + toks[1:], "article"))
            dashed = re.split(r"\s*[–—-]\s*", raw.strip())
            if len(dashed) > 1 and normalize(dashed[0]):
                out.append((tuple(normalize(dashed[0]).split()), "full"))
        else:
            out += [((t,), "partial") for t in toks
                    if len(t) >= _MIN_PARTIAL and t not in _GENERIC_PARTIALS and len(toks) > 1]
    return out


def _is_generic(agent: dict, value: str) -> bool:
    """A value that names the whole collection ("שמאי מכריע" in "החלטות שמאי
    מכריע") says nothing about which decisions are meant."""
    about = normalize(f"{agent.get('name', '')} {agent.get('description', '')}")
    v = normalize(value)
    return len(v.split()) > 1 and f" {v} " in f" {about} "


def build(agent: dict, stored_values: dict[str, set[str]] | None = None) -> Vocabulary:
    """The dictionary for `agent`: schema values plus `stored_values`
    ({field: distinct stored values}, from the catalog)."""
    stored_values = stored_values or {}
    fields, entities, generic = {}, {}, set()
    for f in agent.get("filters", []):
        if f.get("type") != "choice":
            continue
        name = f["name"]
        fields[name] = f.get("label") or name
        schema_vals = [str(v) for v in f.get("values") or []]
        for value in schema_vals + sorted(stored_values.get(name, set())):
            value = str(value).strip() if value is not None else ""
            ident = _identity(name, value)
            if not ident or ident.isdigit() or len(ident) < 2:
                continue
            ent = entities.setdefault((name, ident), Entity(name, ident, value))
            ent.stored.add(value)
            if value in schema_vals and (ent.label not in schema_vals or "  " in ent.label):
                ent.label = value
            if _is_generic(agent, value):
                generic.add((name, ident))

    raw_phrases: dict[tuple[str, ...], list[Phrase]] = {}
    for (name, ident), ent in entities.items():
        if (name, ident) in generic:
            continue
        for toks, kind in _entity_phrases(ent, name):
            bucket = raw_phrases.setdefault(toks, [])
            if not any(p.entity is ent for p in bucket):
                bucket.append(Phrase(toks, ent, kind))
    # A unique leading part of a long name ("פרדס חנה" of "פרדס חנה כרכור").
    starts: dict[tuple[str, ...], set[tuple[str, str]]] = {}
    for toks, bucket in raw_phrases.items():
        for p in bucket:
            if p.entity.field != "DecisiveAppraiser" and p.kind == "full" and len(toks) >= 3:
                for n in range(2, len(toks)):
                    starts.setdefault(toks[:n], set()).add((p.entity.field, p.entity.key))
    for toks, keys in starts.items():
        if len(keys) == 1 and toks not in raw_phrases:
            raw_phrases[toks] = [Phrase(toks, entities[next(iter(keys))], "full")]
    # A partial word that is also a full phrase elsewhere is not a partial.
    for toks, bucket in raw_phrases.items():
        if any(p.kind != "partial" for p in bucket):
            raw_phrases[toks] = [p for p in bucket if p.kind != "partial"]

    phrases: dict[str, list[Phrase]] = {}
    words: set[str] = set()
    by_text: dict[str, list[Phrase]] = {}
    for toks, bucket in raw_phrases.items():
        phrases.setdefault(toks[0], []).extend(bucket)
        words.update(toks)
        by_text.setdefault(" ".join(toks), []).extend(bucket)
    for bucket in phrases.values():
        bucket.sort(key=lambda p: len(p.tokens), reverse=True)
    fuzzy = FuzzyIndex({t: None for t, b in by_text.items()
                        if any(p.kind in ("full", "surname", "article") for p in b)})
    return Vocabulary(fields, entities, phrases, words, fuzzy, by_text, generic)


def stored_forms(vocab: Vocabulary, field_name: str, value: str) -> list[str]:
    """All stored spellings of the entity `value` belongs to (for a dropdown
    choice or an inherited filter); the value itself when it is unknown."""
    ent = vocab.entity_for(field_name, value)
    return ent.values() if ent else [value]


def ui_filters(agent: dict, vocab: Vocabulary) -> list[dict]:
    """The agent's filter schema for the chat UI, with each choice list replaced
    by the dictionary's entities (one label per entity, new stored values included)."""
    out = []
    for f in agent.get("filters", []):
        f = dict(f)
        if f.get("type") == "choice":
            labels = {e.label for (name, _), e in vocab.entities.items() if name == f["name"]}
            # schema values the dictionary skips (e.g. the version "1") stay selectable
            labels |= {v for v in f.get("values") or [] if not vocab.entity_for(f["name"], v)}
            f["values"] = sorted(labels)
        out.append(f)
    return out
