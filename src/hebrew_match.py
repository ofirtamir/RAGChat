"""Hebrew word matching against closed value lists (committees, appraisers…).

Used to find filter values inside a question. Two rules keep it safe:

- Normalise both sides the same way: no niqqud, no geresh/quotes ("נ'" → "נ",
  "ג'קי" → "גקי"), dashes and brackets become spaces, final letters become
  regular letters, and doubled ו/י collapse so full and defective spelling meet
  (וועדה/ועדה, קריית/קרית, בנייה/בניה, תקווה/תקוה).
- Prefix letters are removed only when the shorter word is in the list. A word
  is tried as it is first, so "בית שאן", "בני ברק" and "באר שבע" are never cut;
  "בבית" → "בית" only because "בית" starts a known value.

Approximate matching (one typo) is exposed separately; its callers treat it as
uncertain.
"""
from __future__ import annotations

import re

_NIQQUD = re.compile(r"[֑-ׇ]")
_QUOTES = re.compile(r"[\"'`´׳״‘’“”]")
_SEPARATORS = re.compile(r"[\-‐-―־()\[\]{}<>.,;:!?/\\|*+=_~]")
_FINALS = str.maketrans("ךםןףץ", "כמנפצ")
_DOUBLES = re.compile(r"(ו|י)\1+")
_SPACES = re.compile(r"\s+")

# Prefix chains that can stand before a Hebrew word: ו, then ש/כש/כ, then one
# of מ/ל/ב/כ, then the article ה. "ומה", "שב", "כשה", "לה", "וב"…
_PREFIX_CHAIN = re.compile(r"^ו?(?:כש|ש|כ)?[מלבכ]?ה?$")
_MAX_PREFIX = 4
_MIN_STEM = 2


def normalize(text: str) -> str:
    """The comparable form of a Hebrew string (see the module docstring)."""
    text = _NIQQUD.sub("", text or "")
    text = _QUOTES.sub("", text)
    text = _SEPARATORS.sub(" ", text)
    text = text.translate(_FINALS).lower()
    text = _DOUBLES.sub(r"\1", text)
    return _SPACES.sub(" ", text).strip()


_TO_FINAL = {"כ": "ך", "מ": "ם", "נ": "ן", "פ": "ף", "צ": "ץ"}


def display(text: str) -> str:
    """A normalised string made readable again: final letters at word ends."""
    return " ".join(w[:-1] + _TO_FINAL.get(w[-1], w[-1]) if len(w) > 1 else w for w in text.split())


def tokens(text: str) -> list[str]:
    return normalize(text).split()


def prefix_splits(word: str) -> list[tuple[str, str]]:
    """(prefix, rest) for every valid prefix chain, shortest prefix first.
    The word itself is not included; callers try it before these."""
    out = []
    for n in range(1, min(_MAX_PREFIX, len(word) - _MIN_STEM) + 1):
        prefix = word[:n]
        if _PREFIX_CHAIN.match(prefix):
            out.append((prefix, word[n:]))
    return out


def forms(word: str, known) -> list[tuple[str, int]]:
    """(form, letters removed) to look up for `word`: the word itself, then each
    prefix-less form that is in `known` (a set or dict of words)."""
    out = [(word, 0)]
    for prefix, rest in prefix_splits(word):
        if rest in known:
            out.append((rest, len(prefix)))
    return out


def within_one_edit(a: str, b: str) -> bool:
    """True when a and b differ by at most one insertion, deletion or substitution."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la > lb:
        a, b, la, lb = b, a, lb, la
    i = 0
    while i < la and a[i] == b[i]:
        i += 1
    if la == lb:
        return a[i + 1:] == b[i + 1:]
    return a[i:] == b[i + 1:]


def deletions(word: str) -> set[str]:
    """Every string one deleted letter away (SymSpell-style candidate keys)."""
    return {word[:i] + word[i + 1:] for i in range(len(word))}


class FuzzyIndex:
    """Strings reachable within one edit, found without scanning the list."""

    def __init__(self, items: dict[str, object], min_len: int = 5):
        self.min_len = min_len
        self.items = items
        self._by_key: dict[str, set[str]] = {}
        for s in items:
            if len(s.replace(" ", "")) < min_len:
                continue
            for key in {s} | deletions(s):
                self._by_key.setdefault(key, set()).add(s)

    def lookup(self, text: str) -> list[str]:
        """Known strings one edit from `text` (never `text` itself)."""
        if len(text.replace(" ", "")) < self.min_len:
            return []
        found: set[str] = set()
        for key in {text} | deletions(text):
            found |= self._by_key.get(key, set())
        return sorted(s for s in found if s != text and within_one_edit(s, text))
