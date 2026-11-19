"""Find filter values inside a question, with no model call.

- Block / plot numbers, dates and years: regular expressions.
- Committee, appraiser, appraisal type/version, appraiser role: phrases from the
  value dictionary (src/filter_values), matched over the question's words.
  A word is tried as written first; prefix letters are removed only when the
  shorter word starts a known value (src/hebrew_match). Longer matches win.
- Party names: words of the decision titles (src/filter_catalog), with the same
  prefix rule.

Every match is either certain (a hard filter) or uncertain (only preferred in
ranking): a family name several appraisers share, a committee name that is also
an everyday word with no "ועדה" next to it, a single typo, a lone party word.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from src.filter_catalog import Context
from src.filter_conditions import KEYS_FIELD, Condition
from src.filter_values import AMBIGUOUS_WORDS, Phrase
from src.hebrew_match import display as readable, forms, normalize, prefix_splits

_GERESH = "['׳]"
_NUM_LIST = r"\d{%s}(?:\s*(?:,|ו-?|-)\s*\d{%s})*"
_BLOCK = re.compile(rf"(?<![א-ת])(?:[א-ת]{{0,2}}גוש(?:ים)?|ג{_GERESH})\s*({_NUM_LIST % ('3,6', '3,6')})")
_PLOT = re.compile(rf"(?<![א-ת])(?:[א-ת]{{0,2}}חלק(?:ה|ות)?|ח{_GERESH})\s*({_NUM_LIST % ('1,5', '1,5')})")
_FULL_DATE = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?!\d)")
_BETWEEN = re.compile(r"בין\s*(?:השנים\s*)?((?:19|20)\d{2})\s*(?:ל-?|עד|ו-?|[–—-])\s*((?:19|20)\d{2})"
                      r"|(?<!\d)((?:19|20)\d{2})\s*[–—-]\s*((?:19|20)\d{2})(?!\d)")
_YEAR = re.compile(r"(?<![\d./-])((?:19|20)\d{2})(?![\d./-]?\d)")
_FROM_WORDS = ("מאז", "משנת", "החל", "מ-", "מ")
_AFTER_WORDS = ("אחרי", "לאחר")
_UNTIL_WORDS = ("עד",)
_BEFORE_WORDS = ("לפני",)

COMMITTEE_CUES = frozenset(normalize(w) for w in
                           "ועדה ועדת מקומית מחוזית לתכנון תכנון ובניה בניה עירית עיריית מועצה אזורית".split())
APPRAISER_CUES = frozenset(normalize(w) for w in "שמאי שמאית מכריע מכריעה".split())
PARTY_CUES = frozenset(normalize(w) for w in "נ נגד בעניין עורר עוררת עוררים מבקש מבקשת".split())
QUESTION_STOP = frozenset(normalize(w) for w in """
    מה מי איך האם למה כמה איזה אילו של על את עם לגבי בין כל יש אין גם או אם כי זה זו זאת הוא היא
    החלטה החלטות הכרעה הכרעות שמאי שמאים שומה שומות שווי היטל השבחה פיצויים הפקעה דירה דירות
    מגרש מגרשים בית בתים קרקע זכויות בניה תכנית תוכנית מקדם מקדמים נכס נכסים ועדה מקומית עסקה עסקאות
    נקבע נקבעו קבע קבעה פסק פסקה מכריע מכריעה תביעה תביעות ערר ממוצע שיעור שנה שנים
    יום מיום תאריך מרכז צפון דרום מזרח מערב עיר רחוב שכונה שכונת אזור מקרה מקרים דין
""".split())
PARTY_MAX_KEYS = 300
PARTY_SOFT_MAX = 50
_KIND_RANK = {"full": 0, "article": 0, "surname": 1, "partial": 2}


@dataclass
class Detection:
    hard: list[Condition] = field(default_factory=list)
    soft: list[Condition] = field(default_factory=list)


@dataclass
class _Match:
    start: int
    end: int
    phrases: list[Phrase]
    stripped: int = 0
    fuzzy: bool = False


# ── numbers and dates ────────────────────────────────────────────────────

def _numbers(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\d+", text)))


def _numbers_and_dates(agent: dict, query: str) -> tuple[list[Condition], str]:
    """Block/plot/date conditions, and the query with those parts blanked out."""
    names = {f["name"]: f for f in agent.get("filters", [])}
    out, blanked = [], query
    for name, rx in (("Block", _BLOCK), ("Plot", _PLOT)):
        for m in rx.finditer(query):
            if name in names:
                out.append(Condition(name, "num", _numbers(m.group(1)), label=names[name].get("label", name),
                                     display=", ".join(_numbers(m.group(1))), matched=m.group(0).strip()))
            blanked = blanked.replace(m.group(0), " ")
    # merge repeated Block/Plot mentions
    merged: dict[str, Condition] = {}
    for c in out:
        if c.field in merged:
            merged[c.field].values = list(dict.fromkeys(merged[c.field].values + c.values))
            merged[c.field].display = ", ".join(merged[c.field].values)
        else:
            merged[c.field] = c
    out = list(merged.values())

    date_field = "DecisionDate" if "DecisionDate" in names else next(
        (n for n, f in names.items() if f.get("type") == "date"), None)
    if not date_field:
        return out, blanked
    label = names[date_field].get("label", date_field)
    this_year = date.today().year
    for m in _FULL_DATE.finditer(blanked):
        d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31 and 1 <= mth <= 12 and 2000 <= y <= this_year + 1:
            day = y * 10000 + mth * 100 + d
            out.append(Condition(date_field, "range", [], day, day, label, m.group(0), m.group(0)))
            return out, blanked.replace(m.group(0), " ")
    if m := _BETWEEN.search(blanked):
        a, b = sorted(int(g) for g in m.groups() if g)
        out.append(Condition(date_field, "range", [], a * 10000 + 101, b * 10000 + 1231, label,
                             f"{a}–{b}", m.group(0)))
        return out, blanked.replace(m.group(0), " ")
    years = []
    for m in _YEAR.finditer(blanked):
        y = int(m.group(1))
        if not 2000 <= y <= this_year + 1:
            continue
        before = blanked[max(0, m.start() - 12):m.start()].split()
        prev = before[-1] if before else ""
        prev2 = before[-2] if len(before) > 1 else ""
        years.append((y, prev, prev2, m.group(0)))
    if not years:
        return out, blanked
    start = end = None
    shown = []
    for y, prev, prev2, text in years:
        words = {prev, prev2, prev.rstrip("-"), normalize(prev)}
        if words & set(_AFTER_WORDS):
            start, s = (y + 1) * 10000 + 101, f"אחרי {y}"
        elif words & set(_FROM_WORDS) or prev.startswith("מ-") or (prev == "שנת" and prev2 in _FROM_WORDS):
            start, s = y * 10000 + 101, f"מ-{y}"
        elif words & set(_BEFORE_WORDS):
            end, s = (y - 1) * 10000 + 1231, f"לפני {y}"
        elif words & set(_UNTIL_WORDS) or (prev == "שנת" and prev2 in _UNTIL_WORDS):
            end, s = y * 10000 + 1231, f"עד {y}"
        else:
            start = min(start or y * 10000 + 101, y * 10000 + 101)
            end = max(end or y * 10000 + 1231, y * 10000 + 1231)
            s = str(y)
        shown.append(s)
    out.append(Condition(date_field, "range", [], start, end, label, ", ".join(shown),
                         " ".join(t for *_, t in years)))
    for *_, text in years:
        blanked = blanked.replace(text, " ", 1)
    return out, blanked


# ── dictionary values ────────────────────────────────────────────────────

def _cue_before(toks: list[str], i: int, cues: frozenset, window: int = 3) -> bool:
    for j in range(max(0, i - window), i):
        if toks[j] in cues or any(rest in cues for _, rest in prefix_splits(toks[j])):
            return True
    return False


def _exact_matches(toks: list[str], ctx: Context) -> list[_Match]:
    vocab = ctx.vocab
    found = []
    for i, tok in enumerate(toks):
        for form, stripped in forms(tok, vocab.phrases):
            by_tokens: dict[tuple, list[Phrase]] = {}
            for p in vocab.phrases.get(form, []):
                n = len(p.tokens)
                if tuple([form] + toks[i + 1:i + n]) == p.tokens:
                    by_tokens.setdefault(p.tokens, []).append(p)
            for t, group in by_tokens.items():
                found.append(_Match(i, i + len(t), group, stripped))
    return found


def _fuzzy_matches(toks: list[str], covered: set[int], ctx: Context) -> list[_Match]:
    found = []
    for n in (3, 2, 1):
        for i in range(len(toks) - n + 1):
            span = range(i, i + n)
            if any(j in covered for j in span) or any(toks[j].isdigit() for j in span):
                continue
            if any(toks[j] in QUESTION_STOP or toks[j] in COMMITTEE_CUES | APPRAISER_CUES for j in span):
                continue
            for form, stripped in [(toks[i], 0)] + [(r, len(p)) for p, r in prefix_splits(toks[i])]:
                text = " ".join([form] + toks[i + 1:i + n])
                hits = [p for h in ctx.vocab.fuzzy.lookup(text) for p in ctx.vocab.by_text[h]
                        if p.kind != "partial"]
                if hits:
                    found.append(_Match(i, i + n, hits, stripped, fuzzy=True))
                    covered.update(span)
                    break
    return found


def _select(cands: list[_Match]) -> list[_Match]:
    """Non-overlapping matches, longest first, then fewest prefix letters removed."""
    cands.sort(key=lambda m: (-(m.end - m.start), m.stripped,
                              min(_KIND_RANK.get(p.kind, 3) for p in m.phrases)))
    taken, used = [], set()
    for m in cands:
        if used.isdisjoint(range(m.start, m.end)):
            taken.append(m)
            used.update(range(m.start, m.end))
    return taken


def _value_conditions(toks: list[str], ctx: Context) -> tuple[list[Condition], list[Condition], set[int]]:
    vocab = ctx.vocab
    matches = _select(_exact_matches(toks, ctx))
    covered = {j for m in matches for j in range(m.start, m.end)}
    matches += _fuzzy_matches(toks, covered, ctx)
    hard: dict[str, Condition] = {}
    soft: dict[str, Condition] = {}
    for m in matches:
        f = m.phrases[0].entity.field
        kind = m.phrases[0].kind
        entities = {p.entity.key: p.entity for p in m.phrases}
        if kind == "surname":
            entities = {e.key: e for e in vocab.surname_entities(m.phrases[0])}
        certain = not m.fuzzy and kind != "partial"
        if certain and f == "Committee" and m.end - m.start == 1 and m.phrases[0].entity.key in AMBIGUOUS_WORDS:
            certain = _cue_before(toks, m.start, COMMITTEE_CUES)
        if certain and kind == "surname":
            certain = _cue_before(toks, m.start, APPRAISER_CUES)
        target = hard if certain else soft
        labels = sorted(e.label for e in entities.values())
        display = labels[0] if len(labels) == 1 else f"{readable(' '.join(m.phrases[0].tokens))} ({len(labels)})"
        values = sorted({v for e in entities.values() for v in e.stored})
        matched = readable(" ".join(toks[m.start:m.end]))
        if f in target:
            c = target[f]
            c.values = sorted(set(c.values) | set(values))
            c.display += f", {display}"
            c.matched += f" {matched}"
        else:
            target[f] = Condition(f, "in", values, label=vocab.fields.get(f, f), display=display,
                                  matched=matched, certain=certain)
    for f in hard:
        soft.pop(f, None)
    return list(hard.values()), list(soft.values()), covered | {j for m in matches for j in range(m.start, m.end)}


# ── parties (title words) ────────────────────────────────────────────────

def _party_conditions(toks: list[str], covered: set[int], ctx: Context) -> tuple[list[Condition], list[Condition]]:
    if not ctx.party:
        return [], []
    hits: dict[int, set[str]] = {}
    for i, tok in enumerate(toks):
        if i in covered or tok.isdigit() or tok in QUESTION_STOP or tok in PARTY_CUES or tok in ctx.vocab.words:
            continue
        options = forms(tok, ctx.party)
        form = tok if tok in ctx.party else (options[1][0] if len(options) > 1 else None)
        if not form or len(form) < 3 or form in QUESTION_STOP:
            continue
        hits[i] = ctx.party[form]
    hard, soft = [], []
    i = 0
    while i < len(toks):
        if i not in hits:
            i += 1
            continue
        j = i
        keys = set(hits[i])
        while j + 1 in hits and keys & hits[j + 1]:
            j += 1
            keys &= hits[j]
        words = readable(" ".join(toks[i:j + 1]))
        cue = (i > 0 and toks[i - 1] in PARTY_CUES) or (j + 1 < len(toks) and toks[j + 1] in PARTY_CUES)
        c = Condition(KEYS_FIELD, "keys", sorted(keys), label="צד", display=words, matched=words)
        if keys and len(keys) <= PARTY_MAX_KEYS and (j > i or cue):
            hard.append(c)
        elif keys and len(keys) <= PARTY_SOFT_MAX:
            c.certain = False
            soft.append(c)
        i = j + 1
    return hard, soft


def detect(agent: dict, query: str, ctx: Context) -> Detection:
    """Filter conditions named in `query` (see the module docstring)."""
    numeric, rest = _numbers_and_dates(agent, query or "")
    toks = normalize(rest).split()
    hard, soft, covered = _value_conditions(toks, ctx)
    p_hard, p_soft = _party_conditions(toks, covered, ctx)
    if len(p_hard) > 1:   # several party names: decisions that have them all
        keys = set.intersection(*(set(c.values) for c in p_hard))
        p_hard = [Condition(KEYS_FIELD, "keys", sorted(keys), label="צד",
                            display=" + ".join(c.display for c in p_hard),
                            matched=" ".join(c.matched for c in p_hard))]
    return Detection(numeric + hard + p_hard, soft + p_soft)
