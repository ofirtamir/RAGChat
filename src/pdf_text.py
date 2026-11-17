"""Text of a Hebrew PDF page, rebuilt from the positions of its characters.

Used by src/pdf_parser.py. A word is (x0, y0, x1, y1, visual_text); `_logical`
turns a visual line into reading order."""
from __future__ import annotations

import re

import pymupdf

HEB = re.compile(r"[א-ת]")


# ---------- text fixes ----------

_CP1255_AS_LATIN = re.compile(r"[à-ú]")


def _fix_hebrew(text: str) -> str:
    """Some old Hebrew fonts map letters to their Windows-1255 byte, which comes out as a
    Latin-1 character (0xF0 'ð' instead of 'נ'). Windows-1255 0xE0-0xFA is א-ת in Unicode
    order, so the fix is a shift — applied only to words with no Latin letters."""
    if not _CP1255_AS_LATIN.search(text) or re.search(r"[A-Za-z]", text):
        return text
    return _CP1255_AS_LATIN.sub(lambda m: chr(ord(m.group()) - 0xE0 + 0x5D0), text)


# ---------- words from raw characters ----------
#
# A PDF stores Hebrew in whatever order its producer wrote the glyphs, and PyMuPDF's
# own words follow that order: brackets, dates and punctuation come out scrambled
# ("(מגרש )223", "2026.04.27"), and slanted fonts, whose glyph boxes overlap, look
# like words with no space between them. So words are rebuilt from the characters'
# positions: all characters of a visual line, sorted by x, split at space characters
# (or at a gap, or at a table cell edge). A word's text is *visual* — its characters
# left to right as they sit on the page; `_logical` turns a visual line into reading order.

WORD_GAP = 0.2            # of the font size: a wider gap with no space character still splits words
SPACE_GAP = 0.08          # ... a small gap is enough when a space character sits in it
EDGE_GAP = 0.03           # a table cell edge splits only where the glyphs do not run across it
_RAW_FLAGS = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
_BIDI_MARKS = "".join(map(chr, [0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A), 0xFEFF]))
_BULLETS = re.compile("[" + chr(0xE000) + "-" + chr(0xF8FF) + "]")   # Symbol/Wingdings bullets (private-use area)
_POINTS = re.compile("[" + chr(0x591) + "-" + chr(0x5BD) + chr(0x5BF) + "-" + chr(0x5C7) + "]")   # nikud and cantillation marks


def _visual_lines(page):
    """The page's characters as visual lines: [[(x0, y0, x1, y1, char, size, superscript, run)]],
    each sorted left to right. Rotated text is skipped here (see `_rotated_words`)."""
    units, run = [], 0
    for block in page.get_text("rawdict", flags=_RAW_FLAGS)["blocks"]:
        for line in block.get("lines", []):
            if abs(line["dir"][0] - 1) > 0.01:
                continue
            chars = []
            for span in line["spans"]:
                run += 1        # a span is one run of text in one font, size and slant
                chars += [(*c["bbox"], c["c"], span["size"], bool(span["flags"] & 1), run)
                          for c in span["chars"]]
            if chars:
                x0, y0, x1, y1 = line["bbox"]
                units.append(((y0 + y1) / 2, y1 - y0, chars, x0, x1))
    lines = []
    for yc, h, chars, x0, x1 in sorted(units, key=lambda u: -u[1]):     # tall units first: they define the lines
        for ln in lines:
            # same row: centres close, or a footnote mark (tiny, raised) sitting inside a taller line
            mark = len(chars) <= 3 and h < 0.8 * ln[1] and abs(ln[0] - yc) < 0.5 * ln[1]
            if not (abs(ln[0] - yc) < 0.5 * min(ln[1], h) or mark):
                continue
            # two pieces of one row sit side by side; pieces that overlap sideways at different
            # heights are separate lines squeezed together (tight leading inside a table cell)
            if not mark and any(min(x1, bx1) - max(x0, bx0) > 0.3 * min(x1 - x0, bx1 - bx0)
                                and abs(byc - yc) > 0.15 * h for byc, bx0, bx1 in ln[3]):
                continue
            ln[2].extend(chars)
            ln[3].append((yc, x0, x1))
            break
        else:
            lines.append([yc, h, chars, [(yc, x0, x1)]])
    return [sorted(ln[2], key=lambda c: c[0] + c[2]) for ln in sorted(lines, key=lambda ln: ln[0])]


def _rotated_words(page):
    """Words of rotated text (vertical table headers), as PyMuPDF reads them."""
    out = []
    for block in page.get_text("rawdict", flags=_RAW_FLAGS)["blocks"]:
        for line in block.get("lines", []):
            if abs(line["dir"][0] - 1) <= 0.01:
                continue
            cur = []
            for c in [c for span in line["spans"] for c in span["chars"]] + [None]:
                if c is None or c["c"].isspace():
                    if cur:
                        text = _clean_word("".join(k["c"] for k in cur))
                        if text:    # stored in reading order; `_logical` of it is its visual form
                            out.append((min(k["bbox"][0] for k in cur), min(k["bbox"][1] for k in cur),
                                        max(k["bbox"][2] for k in cur), max(k["bbox"][3] for k in cur),
                                        _logical(text)))
                    cur = []
                else:
                    cur.append(c)
    return out


def _clean_word(text: str) -> str:
    return _fix_hebrew(_BULLETS.sub("•", _POINTS.sub("", text))).replace("־", "-")


_SWAP_BRACKETS = str.maketrans("()[]{}", ")(][}{")
_LOGICAL_PAIR = re.compile(r"[)\]][^()\[\]]*[א-ת][^()\[\]]*[(\[]")   # ")223 שרגמ(" seen left to right
_GLYPH_PAIR = re.compile(r"[(\[][^()\[\]]*[א-ת][^()\[\]]*[)\]]")


def _bracket_votes(visual_lines) -> int:
    """> 0 when the page's brackets are stored as drawn shapes rather than as characters:
    then "(מגרש 223)" reads ")מגרש 223(" and every bracket must be swapped. Hebrew
    in brackets, seen left to right, is ")...(" when stored as characters."""
    votes = 0
    for line in visual_lines:
        text = "".join(c[4] for c in line)
        votes += len(_GLYPH_PAIR.findall(text)) - len(_LOGICAL_PAIR.findall(text))
    return votes


def _glyph_overlap(visual_lines) -> dict:
    """Per run of text: how far a glyph's box runs into the next glyph of the same word.
    0 for ordinary text; positive for slanted text, whose boxes are wider than the
    glyph's advance (so words look glued); negative for letter-spaced text. Adding it
    to the distance between two boxes gives the real gap between the glyphs."""
    seen = {}
    for line in visual_lines:
        prev = None
        for c in line:
            if c[4].isspace():
                prev = None
            else:
                if prev is not None and prev[7] == c[7] and prev[2] - c[0] > -WORD_GAP * c[5]:
                    seen.setdefault(c[7], []).append(prev[2] - c[0])
                prev = c
    # from one or two pairs only an overlap is believed (a slanted run can be two words long)
    return {run: sorted(v)[len(v) // 2] if len(v) >= 3 else max(0.0, sorted(v)[len(v) // 2])
            for run, v in seen.items()}


def _join_spaced_letters(words):
    """ "ה ח ל ט ת  ש מ א י ת" (a letter-spaced title) -> "החלטת שמאית": inside a run of
    single letters, the wider gaps are the word breaks. `words` are left to right."""
    out, i = [], 0
    while i < len(words):
        j = i
        while j < len(words) and len(words[j][4]) == 1 and HEB.match(words[j][4]):
            j += 1
        if j - i < 4:
            out.extend(words[i:max(j, i + 1)])
            i = max(j, i + 1)
            continue
        run = words[i:j]
        gaps = sorted(b[0] - a[2] for a, b in zip(run, run[1:]))
        limit = 1.6 * gaps[len(gaps) // 2] + 0.5
        cur = run[0]
        for w in run[1:]:
            if w[0] - cur[2] <= limit:
                cur = (cur[0], min(cur[1], w[1]), w[2], max(cur[3], w[3]), cur[4] + w[4])
            else:
                out.append(cur)
                cur = w
        out.append(cur)
        i = j
    return out


def _page_words(page, cuts=(), cache: dict | None = None):
    """Words rebuilt from character positions: (x0, y0, x1, y1, visual_text).
    `cuts` are table cell edges (x, y0, y1): a word never crosses the edge of its own cell.
    `cache` keeps each page's characters between the two passes over the document."""
    if cache is None:
        cache = {}
    if page.number not in cache:
        cache[page.number] = [_visual_lines(page), _rotated_words(page), None]
    visual_lines, rotated, mirrored = cache[page.number]
    if mirrored is None:
        mirrored = _bracket_votes(visual_lines) > 0
    overlap = _glyph_overlap(visual_lines)
    words = []

    def emit(chars, into):
        if not chars:
            return
        text = _clean_word("".join(c[4] for c in chars))
        if not text:
            return
        if mirrored:
            text = text.translate(_SWAP_BRACKETS)
        if all(c[6] for c in chars) and text.isdigit() and len(text) <= 3:
            text = f"]{text}["                                  # footnote mark: reads "[1]"
        into.append((min(c[0] for c in chars), min(c[1] for c in chars),
                     max(c[2] for c in chars), max(c[3] for c in chars), text))

    for line in visual_lines:
        yc = (line[0][1] + line[0][3]) / 2
        edges = sorted(x for x, y0, y1 in cuts if y0 <= yc <= y1)
        line_words, cur, spaced = [], [], False
        for c in line:
            ch = c[4]
            if ch.isspace():
                spaced = True       # a space character counts only if there is a gap where it sits
                continue
            if ch in _BIDI_MARKS:
                continue
            if cur:
                prev = cur[-1]
                if ch == prev[4] and abs(c[0] - prev[0]) < 0.1 * c[5]:
                    continue                                    # the same glyph struck twice (fake bold)
                a, b = (prev[0] + prev[2]) / 2, (c[0] + c[2]) / 2
                mark = c[6] and ch.isdigit()
                size = max(c[5], prev[5])
                gap = c[0] - prev[2] + overlap.get(prev[7], 0)
                if gap > WORD_GAP * size or (spaced and gap > SPACE_GAP * size)                         or mark != (prev[6] and prev[4].isdigit())                         or (gap > EDGE_GAP * size and any(a < x <= b for x in edges)):
                    emit(cur, line_words)
                    cur = []
            spaced = False
            cur.append(c)
        emit(cur, line_words)
        words += _join_spaced_letters(line_words)
    return words + rotated


# ---------- visual order -> reading order ----------

_SEPARATORS = ".,/:-–"     # stay inside a number or a Latin run when both neighbours belong to it
_NUMBER_SIGNS = "%‰$₪€£#°±"  # attach to the number next to them


def _logical(visual: str) -> str:
    """A visual line (characters left to right) -> reading order, for a Hebrew document.

    Everything reads right to left except numbers and Latin text, which keep their
    left-to-right order as a block: "27.04.2026", "1,560,000 ₪", "Tel Aviv 5". This is
    the Unicode bidi algorithm's result for right-to-left text, run backwards."""
    n = len(visual)
    ltr = [c.isdigit() or ("a" <= c.lower() <= "z") for c in visual]
    latin = [("a" <= c.lower() <= "z") for c in visual]
    strong = list(ltr)
    for i, c in enumerate(visual):
        if c in _SEPARATORS and 0 < i < n - 1 and strong[i - 1] and strong[i + 1]:
            ltr[i] = True
        elif c in _NUMBER_SIGNS and ((i > 0 and visual[i - 1].isdigit()) or (i < n - 1 and visual[i + 1].isdigit())):
            ltr[i] = True
        elif c == "," and 0 < i < n - 2 and visual[i - 1].isdigit() and visual[i + 1] == " " and visual[i + 2].isdigit():
            # "211, 212, 311": a comma that hugs the number on its left was typed left to
            # right (right-to-left text shows it as "311 ,212 ,211"), so the list keeps its order
            ltr[i] = ltr[i + 1] = True
    # a digit that follows Latin text belongs to it ("Herzl 5"); so does whatever sits
    # between two Latin pieces ("urban planning", "A - B")
    anchor = None                      # last Latin-run character with no Hebrew since
    for i in range(n):
        if HEB.match(visual[i]):
            anchor = None
        elif strong[i] and (latin[i] or anchor is not None):
            if anchor is not None:
                for k in range(anchor + 1, i):
                    ltr[k] = True
            anchor = i
    tokens, i = [], 0
    while i < n:
        j = i + 1
        if ltr[i]:
            while j < n and ltr[j]:
                j += 1
        tokens.append(visual[i:j])
        i = j
    return "".join(reversed(tokens))


# ---------- lines from word boxes ----------

def _group_lines(words):
    """words: (x0, y0, x1, y1, text). Group by vertical overlap into visual lines, top to bottom."""
    lines = []
    for w in sorted(words, key=lambda w: (w[1] + w[3]) / 2):
        yc, h = (w[1] + w[3]) / 2, w[3] - w[1]
        for ln in lines:
            if abs(ln["yc"] - yc) < 0.5 * min(ln["h"], h):
                ln["w"].append(w)
                break
        else:
            lines.append({"yc": yc, "h": h, "w": [w]})
    return lines


def _line_text(words) -> str:
    """One visual line of words -> its text in reading order."""
    return _logical(" ".join(w[4] for w in sorted(words, key=lambda w: w[0] + w[2])))


def _words_to_text(words) -> str:
    return " ".join(_line_text(ln["w"]) for ln in _group_lines(words)).strip()


def _inside(w, rect, pad=1.0) -> bool:
    cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
    return rect[0] - pad <= cx <= rect[2] + pad and rect[1] - pad <= cy <= rect[3] + pad
