"""Hebrew normalisation for keyword (lexical) search.

Hebrew glues one-letter prefixes to words: "בתל אביב", "והשבחה", "לגוש".
A keyword index over raw words would not match "תל" or "השבחה". Each word is
indexed as itself plus the forms with up to two leading prefix letters
removed, and queries go through the same function, so either side can carry
the prefix. Niqqud and punctuation are dropped; numbers stay as they are, and
numbers joined by "/" or "-" are also indexed as their parts.
"""
from __future__ import annotations

import re

_PREFIXES = set("והבלמשכ")
_NIQQUD = re.compile(r"[֑-ׇ]")
_TOKEN = re.compile(r"[א-ת]+(?:[\"'״׳][א-ת]+)*|[A-Za-z]+|\d+(?:[.,/\-]\d+)*")
_MIN_STEM = 2


def _variants(word: str) -> list[str]:
    word = word.replace('"', "").replace("'", "").replace("״", "").replace("׳", "")
    out = [word]
    if word[0].isdigit():
        parts = re.split(r"[/\-]", word)
        if len(parts) > 1:
            out += [p for p in parts if p]
        return out
    stem = word
    for _ in range(2):
        if len(stem) - 1 >= _MIN_STEM and stem[0] in _PREFIXES:
            stem = stem[1:]
            out.append(stem)
        else:
            break
    return out


def keyword_terms(text: str) -> list[str]:
    """Searchable terms for `text`, in order, variants included."""
    text = _NIQQUD.sub("", text or "").lower()
    terms: list[str] = []
    for m in _TOKEN.finditer(text):
        terms.extend(_variants(m.group(0)))
    return terms


def keyword_text(text: str) -> str:
    """The text as stored in the keyword index (space-separated terms)."""
    return " ".join(keyword_terms(text))


def keyword_query(text: str) -> str:
    """A keyword query for `text`: its distinct terms, longest-first."""
    seen: dict[str, None] = {}
    for term in keyword_terms(text):
        if len(term) >= 2 or term.isdigit():
            seen.setdefault(term, None)
    return " ".join(sorted(seen, key=len, reverse=True)[:32])


_BLOCK = re.compile(r"גוש\s*(\d{3,6})")
_PLOT = re.compile(r"חלק(?:ה|ות)?\s*(\d{1,5})")


def extract_block_plot(query: str) -> dict:
    """Block/plot numbers named in a question ("גוש 6426 חלקה 484")."""
    found = {}
    if m := _BLOCK.search(query or ""):
        found["Block"] = m.group(1)
    if m := _PLOT.search(query or ""):
        found["Plot"] = m.group(1)
    return found
