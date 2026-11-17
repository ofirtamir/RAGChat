"""Hebrew-aware PDF -> Markdown parser built on PyMuPDF.

The appraisal decisions are born-digital Hebrew PDFs whose text layer is stored
in visual order, which plain extraction (pypdf, PyMuPDF get_text) mangles:
reversed table headers, numbers split into reversed pieces ("000 , 70" for
70,000), mirrored parentheses, and tables flattened into loose lines.

Instead of trusting the text layer's order, text is rebuilt from the characters'
positions:
- the characters of each visual line, sorted by x, are split into words at space
  characters; a line is then put in reading order with the bidi rules (Hebrew
  right-to-left; numbers and Latin text left-to-right as a block) — src/pdf_text.py;
- tables use PyMuPDF's cell grid; rows it cut in pieces are put back, phantom
  columns created by merged header cells are folded away and multi-row headers
  are combined ("parent - child") — src/pdf_tables.py;
- a table used as page layout (a cell full of paragraphs, often with a real table
  inside) is read as running text, with the inner table in its place;
- lines become paragraphs that break at headings and list items;
- recurring letterhead/footer lines are dropped.

Each table is validated; tables that fail come back with `problems` so a
caller can route them to a stronger parser.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from src.pdf_tables import ParsedTable, _build_table, _fold_fragments, _header_rows, _is_num  # noqa: F401
from src.pdf_text import (  # noqa: F401
    _bracket_votes, _fix_hebrew, _group_lines, _inside, _line_text, _logical, _page_words)

LONG_CELL = 600           # characters: a table cell this long holds running text, not a value


@dataclass
class ParsedImage:
    """An embedded picture that may hold text the text layer doesn't have (a pasted table)."""
    index: int                                # position in the document, used in the marker
    page: int                                 # 0-based
    bbox: tuple[float, float, float, float]
    digest: str                               # md5 of the image bytes (cache key)

    @property
    def marker(self) -> str:
        return IMAGE_MARKER.format(self.index)


@dataclass
class ParsedPage:
    number: int  # 0-based
    markdown: str
    tables: list[ParsedTable] = field(default_factory=list)
    images: list[ParsedImage] = field(default_factory=list)


IMAGE_MARKER = "<<image:{}>>"
IMAGE_MARKER_RE = re.compile(r"<<image:\d+>>")
MIN_IMAGE_PT = (100, 30)     # smaller pictures are icons, stamps, signatures
MIN_IMAGE_AREA = 0.02        # share of the page
MAX_WORDS_OVER_IMAGE = 4     # text drawn over a picture means it is a background, not content


# ---------- pages ----------

def _norm(s: str) -> str:
    """Letters only, so page numbers and punctuation order don't break the match."""
    return "".join(re.findall(r"[א-תA-Za-z]", s))


def _furniture(doc, cache: dict) -> set[str]:
    """Top/bottom-zone lines that recur on at least half the pages (letterhead, footer)."""
    counts = Counter()
    for page in doc:
        h = page.rect.height
        seen = set()
        for ln in _group_lines(_page_words(page, cache=cache)):
            if ln["yc"] < 0.18 * h or ln["yc"] > 0.82 * h:
                key = _norm(_line_text(ln["w"]))
                if key:
                    seen.add(key)
        counts.update(seen)
    return {k for k, n in counts.items() if n >= max(3, 0.5 * len(doc))}


SHORT_LINE = 0.3    # a line whose left end stops this far (share of the text width) from the margin
# "1." "6.1." "א." "(ב)" "ג)" "(3)" or a bullet, then a space: the start of a list item or heading
_ITEM = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2}){0,3}\.|\(?[א-ת]{1,2}[.)]|\(\d{1,2}\)|[•▪■◼○●*–-])\s")
_GLUED_NUMBER = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){1,3}\.?)(?=[א-ת])")
_NUMBERED = re.compile(r"^\d{1,2}(?:\.\d{1,2}){1,3}\s+[א-ת]")     # "6.1 מינוי", only after a finished line


def _is_noise_line(text: str, yc: float, page_height: float) -> bool:
    if re.fullmatch(r"[_\-−–\s]*", text):
        return True
    return yc > 0.82 * page_height and re.fullmatch(r"[\d\s_\-−–]*", text) is not None


def _image_digests(doc) -> tuple[Counter, dict]:
    """How many pages each picture appears on (a letterhead logo repeats on every page),
    and each page's picture list (hashing is slow, so it is done once)."""
    seen, infos = Counter(), {}
    for page in doc:
        infos[page.number] = page.get_image_info(hashes=True)
        seen.update({_digest(i) for i in infos[page.number]})
    return seen, infos


def _digest(info) -> str:
    d = info.get("digest", b"")
    return d.hex() if isinstance(d, bytes) else str(d)


def _content_images(page, words, pictures: tuple[Counter, dict], start: int) -> list[ParsedImage]:
    """Pictures big enough to hold a table or a paragraph, shown once in the document,
    with no text layer drawn over them."""
    repeated, infos = pictures
    found, area = [], page.rect.width * page.rect.height
    for info in infos[page.number]:
        rect = pymupdf.Rect(info["bbox"]) & page.rect
        digest = _digest(info)
        if rect.is_empty or rect.width < MIN_IMAGE_PT[0] or rect.height < MIN_IMAGE_PT[1]:
            continue
        if rect.width * rect.height < MIN_IMAGE_AREA * area or repeated[digest] > 1:
            continue
        if sum(1 for w in words if _inside(w, rect)) > MAX_WORDS_OVER_IMAGE:
            continue
        if any(i.digest == digest for i in found):
            continue
        found.append(ParsedImage(index=start + len(found), page=page.number, bbox=tuple(rect), digest=digest))
    return found


def _paragraphs(words, furniture=(), page_height: float = 0.0) -> list[tuple[float, str]]:
    """Words of one column of running text -> (top y, text) paragraphs.

    A paragraph ends at a vertical gap, at a line that stops well short of the left
    margin (its last line, or a heading), and before a numbered / lettered / bulleted
    item — so "1. פרטי המקרקעין" starts its own block instead of drowning in the text
    around it (the section chunker looks for exactly these lines)."""
    lines = []
    for ln in _group_lines(words):
        text = _GLUED_NUMBER.sub(lambda m: m.group(1) + " ", _line_text(ln["w"]))
        if _norm(text) in furniture or (page_height and _is_noise_line(text, ln["yc"], page_height)):
            continue
        lines.append((ln, text))
    left = min((w[0] for ln, _ in lines for w in ln["w"]), default=0)
    right = max((w[2] for ln, _ in lines for w in ln["w"]), default=0)
    blocks, para, prev, prev_text = [], [], None, ""
    for ln, text in lines:
        if para:
            short = min(w[0] for w in prev["w"]) - left > SHORT_LINE * (right - left)
            ended = short or prev_text.rstrip().endswith((".", ":", ";"))
            if ln["yc"] - prev["yc"] > 1.9 * max(ln["h"], prev["h"]) or short                     or _ITEM.match(text) or (ended and _NUMBERED.match(text)):
                blocks.append((para[0][0]["yc"], " ".join(t for _, t in para)))
                para = []
        para.append((ln, text))
        prev, prev_text = ln, text
    if para:
        blocks.append((para[0][0]["yc"], " ".join(t for _, t in para)))
    return blocks


def _cells(table) -> list[tuple]:
    return list({tuple(c) for row in table.rows for c in row.cells if c})


def _parse_page(page, furniture, pictures=None, image_start: int = 0, cache: dict | None = None) -> ParsedPage:
    found = [t for t in page.find_tables().tables if t.row_count >= 2 and t.col_count >= 2]
    cuts = {(round(x, 1), cell[1], cell[3]) for t in found for cell in _cells(t) for x in (cell[0], cell[2])}
    words = _page_words(page, cuts, cache)
    blocks, tables = [], []
    images = _content_images(page, words, pictures, image_start) if pictures is not None else []
    for img in images:
        blocks.append((img.bbox[1], img.marker))

    # A "layout table" is a table used as page layout: a cell holds paragraphs of text,
    # often with a real table inside it. As a Markdown table it would be one endless
    # row with the inner table's numbers poured into it, so it is read as running
    # text instead, cell by cell (right to left), with the inner table in its place.
    rects = {id(t): pymupdf.Rect(t.bbox) for t in found}
    inner = {id(t) for t in found
             if any(o is not t and rects[id(o)].contains(rects[id(t)]) for o in found)}
    layout = {id(t) for t in found if id(t) not in inner and (
        any(id(i) in inner and rects[id(t)].contains(rects[id(i)]) for i in found)
        or any(sum(len(w[4]) + 1 for w in words if _inside(w, c, 0.5)) > LONG_CELL for c in _cells(t)))}
    width = page.rect.width
    for t in found:
        if id(t) not in layout:
            parsed = _build_table(t, words)
            tables.append(parsed)
            blocks.append((t.bbox[1], parsed.markdown))
            continue
        inside = [rects[id(i)] for i in found if id(i) in inner and rects[id(t)].contains(rects[id(i)])]
        mine = [w for w in words if _inside(w, t.bbox) and not any(_inside(w, r, 2) for r in inside)]
        taken = set()
        for cell in sorted(_cells(t), key=lambda c: (c[1], -c[2])):
            cell_words = [w for w in mine if id(w) not in taken and _inside(w, cell, 0.5)]
            taken.update(id(w) for w in cell_words)
            # cells of one row start at the same height: the rightmost is read first
            blocks += [(y + (width - cell[2]) * 1e-4, text) for y, text in _paragraphs(cell_words)]
        blocks += _paragraphs([w for w in mine if id(w) not in taken])

    table_rects = list(rects.values())
    free = [w for w in words if not any(_inside(w, r, 2) for r in table_rects)]
    blocks += _paragraphs(free, furniture, page.rect.height)
    blocks.sort(key=lambda b: b[0])
    return ParsedPage(number=page.number, markdown="\n\n".join(b[1] for b in blocks),
                      tables=tables, images=images)


def parse_pdf(source: str | Path | bytes, image_markers: bool = False) -> list[ParsedPage]:
    """Parse every page of a PDF (path or raw bytes) into Markdown.

    With image_markers, pictures that may hold content (a table pasted as an image)
    are listed in `ParsedPage.images` and stand in the Markdown as a marker line at
    their position; the caller must replace or strip every marker
    (`strip_image_markers`)."""
    doc = pymupdf.open(stream=source, filetype="pdf") if isinstance(source, bytes) else pymupdf.open(source)
    with doc:
        cache = {}
        furniture = _furniture(doc, cache)
        votes = {n: _bracket_votes(entry[0]) for n, entry in cache.items()}
        overall = sum(votes.values()) > 0
        for n, entry in cache.items():
            entry[2] = votes[n] > 0 if votes[n] else overall
        pictures = _image_digests(doc) if image_markers else None
        pages, count = [], 0
        for page in doc:
            parsed = _parse_page(page, furniture, pictures, count, cache)
            cache.pop(page.number, None)
            count += len(parsed.images)
            pages.append(parsed)
        return pages


def strip_image_markers(markdown: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", IMAGE_MARKER_RE.sub("", markdown)).strip()


def pdf_to_markdown(source: str | Path | bytes) -> str:
    return "\n\n".join(p.markdown for p in parse_pdf(source) if p.markdown).strip()
