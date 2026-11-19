"""Hebrew normalisation for keyword (lexical) search.

Hebrew glues one-letter prefixes to words: "בתל אביב", "והשבחה", "לגוש".
A keyword index over raw words would not match "תל" or "השבחה". Each word is
indexed as itself plus the forms with up to two leading prefix letters
removed, and queries go through the same function, so either side can carry
the prefix. Only real prefix sequences are removed ("וה", not "הש"), and a
short list of common words whose first letter belongs to the word ("היטל",
"מועד") is never cut. Niqqud and punctuation are dropped; numbers stay as they are, and
numbers joined by "/" or "-" are also indexed as their parts.
"""
from __future__ import annotations

import re

_PREFIXES = set("והבלמשכ")
# Which prefix letter may follow which: "וה", "שב", "מה", "כש"… but never "הש"
# or "המ" ("המועד" is ה + מועד, not ה + מ + ועד). ב/ל absorb the article.
_NEXT_PREFIX = {"ו": set("בלמשכה"), "ש": set("בלמכה"), "מ": {"ה"}, "כ": {"ש", "ה"}}
# Common words in the decisions whose first letter is part of the word, not a
# prefix: stripping it gives a non-word ("היטל" → "יטל") or another word
# ("מועד" → "ועד", "שמאי" → "מאי") and only adds noise to keyword search.
_WHOLE_WORDS = frozenset("""
    היטל היטלי היתר היתרים הסכם הסכמה הפקעה השבחה הכרעה החלטה הערכה הקלה הקלות השגה
    הודעה הוראה הוראות הנחה הפחתה היקף הליך הריסה התנגדות התחדשות הוצאות הגשה
    מועד מועדים מגרש מגרשים מבנה מבנים מקרקעין משרד משרדים מחיר מחירים מכירה מימוש
    מקדם מקדמי מדד מתחם מגורים מסחר מבקש מבקשת משיבה מכר מלא
    שמאי שמאית שמאים שומה שומות שווי שטח שטחי שימוש
    בניה בנייה ביטול בית בנין בניין
    לוח כלל
""".split())
# Never emitted as a variant: function words that would match nearly every chunk.
_NOT_VARIANTS = frozenset("לא עד ועד על אם כי את גם כל מה או יש זה זו פי".split())
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
    stem, allowed = word, _PREFIXES
    for _ in range(2):
        if stem in _WHOLE_WORDS or len(stem) - 1 < _MIN_STEM or stem[0] not in allowed:
            break
        allowed = _NEXT_PREFIX.get(stem[0], set())
        stem = stem[1:]
        if stem not in _NOT_VARIANTS:
            out.append(stem)
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


_DOUBLED = re.compile(r"(ו|י)\1+")
_MAX_SPELLINGS = 16


def spellings(word: str) -> list[str]:
    """Other common spellings of a Hebrew word: full and defective spelling
    differ by a doubled ו/י ("קרית"/"קריית", "ועדה"/"וועדה", "בניה"/"בנייה").
    Documents are indexed as written, so the query asks for both forms."""
    if len(word) < 4 or not word[0].isalpha() or word.isascii():
        return []
    out = []
    collapsed = _DOUBLED.sub(r"\1", word)
    if collapsed != word:
        out.append(collapsed)
    if word.startswith("ו") and not word.startswith("וו"):
        out.append("ו" + word)
    for i in range(1, len(collapsed) - 1):
        if collapsed[i] in "וי":
            out.append(collapsed[:i + 1] + collapsed[i] + collapsed[i + 1:])
    return [w for w in dict.fromkeys(out) if w != word][:2]


def keyword_query(text: str) -> str:
    """A keyword query for `text`: its distinct terms, longest-first, then the
    other spellings of those terms (no re-indexing needed)."""
    seen: dict[str, None] = {}
    for term in keyword_terms(text):
        if len(term) >= 2 or term.isdigit():
            seen.setdefault(term, None)
    main = sorted(seen, key=len, reverse=True)[:32]
    extra = [s for t in main for s in spellings(t) if s not in seen]
    return " ".join(main + list(dict.fromkeys(extra))[:_MAX_SPELLINGS])


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
