"""Hebrew-aware PDF -> Markdown parser built on PyMuPDF.

The appraisal decisions are born-digital Hebrew PDFs whose text layer is stored
in visual order, which plain extraction (pypdf, PyMuPDF get_text) mangles:
reversed table headers, numbers split into reversed pieces ("000 , 70" for
70,000), mirrored parentheses, and tables flattened into loose lines.

Instead of trusting the text layer's order, text is rebuilt from word boxes:
- words are grouped into visual lines and read right-to-left;
- touching non-Hebrew pieces (split numbers, "$11.6") are glued left-to-right;
- tables use PyMuPDF's cell grid, then phantom columns created by merged header
  cells are folded away and multi-row headers are combined ("parent - child");
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

HEB = re.compile(r"[א-ת]")
NUMCELL = re.compile(r"^[\s\d.,\-−%$₪/X×()+]*\d[\s\d.,\-−%$₪/X×()+]*$")
TOUCH_PT = 1.2  # horizontal gap below this means two words are pieces of one visual token
MIN_COVERAGE = 0.97
MAX_COLUMNS = 20


@dataclass
class ParsedTable:
    markdown: str
    problems: list[str] = field(default_factory=list)


@dataclass
class ParsedPage:
    number: int  # 0-based
    markdown: str
    tables: list[ParsedTable] = field(default_factory=list)


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


def _fix_parens(tok: str) -> str:
    """Mirrored glyphs from RTL producers: ')19X12(' -> '(19X12)', ')(text' -> '(text)'."""
    if tok.startswith(")(") and len(tok) > 2 and "(" not in tok[2:] and ")" not in tok[2:]:
        return "(" + tok[2:] + ")"
    if tok.startswith(")") and tok.endswith("(") and tok.count(")") == tok.count("("):
        return tok.translate(str.maketrans("()", ")("))
    return tok


def _line_text(words) -> str:
    words = sorted(words, key=lambda w: -w[2])
    clusters, cur = [], [words[0]]
    for w in words[1:]:
        if cur[-1][0] - w[2] < TOUCH_PT:
            cur.append(w)
        else:
            clusters.append(cur)
            cur = [w]
    clusters.append(cur)
    tokens = []
    for c in clusters:
        if any(HEB.search(w[4]) for w in c):
            tok = "".join(w[4] for w in sorted(c, key=lambda w: -w[2]))
        else:
            tok = "".join(w[4] for w in sorted(c, key=lambda w: w[0]))
            tok = re.sub(r"^([.,:;])(\w.*)$", r"\2\1", tok)  # sentence-final mark landed in front
        tokens.append(_fix_parens(tok))
    return " ".join(tokens)


def _words_to_text(words) -> str:
    return " ".join(_line_text(ln["w"]) for ln in _group_lines(words)).strip()


def _inside(w, rect, pad=1.0) -> bool:
    cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
    return rect[0] - pad <= cx <= rect[2] + pad and rect[1] - pad <= cy <= rect[3] + pad


# ---------- tables ----------

def _is_num(s: str) -> bool:
    """A number, optionally with a short unit (מ"ר, ל"י, ש"ח), or an empty-value dash."""
    if re.fullmatch(r"\s*[-−–—]{1,3}\s*", s):
        return True
    if not re.search(r"\d", s):
        return False
    if NUMCELL.match(s):
        return True
    return len(HEB.findall(s)) <= 4 and len(re.findall(r"[A-Za-z]", s)) <= 1


def _numeric_row(row) -> bool:
    filled = [x for x in row if x]
    return bool(filled) and sum(_is_num(x) for x in filled) / len(filled) >= 0.5


def _header_rows(grid) -> tuple[list[int], int]:
    """Header = text rows directly above the longest run of numeric rows."""
    kinds = ["N" if _numeric_row(r) else ("E" if not any(r) else "T") for r in grid]
    best_len, body_start, start = 0, -1, None
    for i, k in enumerate(kinds + ["T"]):
        if k == "N" and start is None:
            start = i
        elif k != "N" and start is not None:
            if i - start > best_len:
                best_len, body_start = i - start, start
            start = None
    if body_start < 0:
        return [0], 1
    hdr, j = [], body_start - 1
    while j >= 0 and kinds[j] == "T":
        hdr.insert(0, j)
        j -= 1
    if not hdr and body_start > 0:
        hdr = [body_start - 1]
    return hdr, body_start


def _build_table(table, words) -> ParsedTable:
    rows = []
    if table.header.external:
        rows.append(list(table.header.cells))
    rows += [list(r.cells) for r in table.rows]
    ncol = max(len(r) for r in rows)
    rows = [r + [None] * (ncol - len(r)) for r in rows]

    # each column's x-range comes from the narrowest cell seen at that index
    colx = []
    for c in range(ncol):
        boxes = [r[c] for r in rows if r[c]]
        colx.append(min(boxes, key=lambda b: b[2] - b[0]) if boxes else None)

    in_table = [w for w in words if _inside(w, table.bbox)]
    used = set()
    grid, spans = [], []
    for r in rows:
        texts, row_spans = [""] * ncol, [[k] for k in range(ncol)]
        for c, box in enumerate(r):
            if not box:
                continue
            cell_words = [w for w in in_table if _inside(w, box, 0.5)]
            if not cell_words:
                continue
            used.update(id(w) for w in cell_words)
            span = [k for k in range(ncol)
                    if colx[k] and box[0] - 1 <= (colx[k][0] + colx[k][2]) / 2 <= box[2] + 1] or [c]
            # a merged body cell's value belongs to the column it visually sits in
            cx = sum((w[0] + w[2]) / 2 for w in cell_words) / len(cell_words)
            target = next((k for k in span if colx[k][0] <= cx <= colx[k][2]), span[0])
            texts[target] = _words_to_text(cell_words)
            row_spans[target] = span
        grid.append(texts)
        spans.append(row_spans)
    coverage = len(used) / max(1, len(in_table))

    hdr, _ = _header_rows(grid)

    # Spread merged header cells across their span, tagged with the span, so stacked
    # lines of one label join with a space and parent/child labels with " - ".
    for h in hdr:
        for c, txt in enumerate(list(grid[h])):
            if txt:
                tag = f"{txt}\x00{min(spans[h][c])}-{max(spans[h][c])}"
                for k in spans[h][c]:
                    if not grid[h][k] or k == c:
                        grid[h][k] = tag

    # Fold away empty and phantom columns: merge neighbours that are never both
    # filled in the same row (identical spread header text doesn't count).
    nrow = len(grid)
    cols = [[grid[r][c] for r in range(nrow)] for c in range(ncol)]
    cols = [c for c in cols if any(c)]
    merged = True
    while merged and len(cols) > 1:
        merged = False
        for c in range(len(cols) - 1):
            a, b = cols[c], cols[c + 1]
            if all(not (a[r] and b[r]) or (r in hdr and a[r] == b[r]) for r in range(nrow)):
                cols[c] = [a[r] or b[r] for r in range(nrow)]
                del cols[c + 1]
                merged = True
                break
    cols.reverse()  # rightmost column first, the Hebrew reading order

    header = []
    for col in cols:
        label, last_span = "", None
        for h in hdr:
            if not col[h]:
                continue
            txt, span = col[h].split("\x00")
            if not label:
                label = txt
            elif span == last_span:
                label += " " + txt
            elif txt not in label:
                label += " - " + txt
            last_span = span
        header.append(label)

    caption, body = [], []
    first_hdr = hdr[0] if hdr else 0
    for r in range(nrow):
        if r in hdr:
            continue
        vals = [col[r] for col in cols]
        if not any(vals):
            continue
        if r < first_hdr and sum(1 for v in vals if v) == 1:
            caption.append(next(v for v in vals if v))
        else:
            body.append(vals)

    problems = []
    if coverage < MIN_COVERAGE:
        problems.append(f"word coverage {coverage:.0%}")
    if not 2 <= len(cols) <= MAX_COLUMNS:
        problems.append(f"{len(cols)} columns")
    if not body:
        problems.append("no data rows")
    unlabeled = sum(1 for i, col in enumerate(cols)
                    if not header[i] and any(col[r] for r in range(nrow) if r not in hdr))
    if unlabeled:
        problems.append(f"{unlabeled} columns without header")

    def md_row(values):
        return "| " + " | ".join(v.replace("|", "/") for v in values) + " |"

    md = f"**{' '.join(caption)}**\n\n" if caption else ""
    md += md_row(header) + "\n|" + "---|" * len(header) + "\n" + "\n".join(md_row(v) for v in body)
    return ParsedTable(markdown=md, problems=problems)


# ---------- pages ----------

def _norm(s: str) -> str:
    """Letters only, so page numbers and punctuation order don't break the match."""
    return "".join(re.findall(r"[א-תA-Za-z]", s))


def _furniture(doc) -> set[str]:
    """Top/bottom-zone lines that recur on at least half the pages (letterhead, footer)."""
    counts = Counter()
    for page in doc:
        h = page.rect.height
        seen = set()
        for ln in _group_lines([w[:5] for w in page.get_text("words")]):
            if ln["yc"] < 0.18 * h or ln["yc"] > 0.82 * h:
                key = _norm(_line_text(ln["w"]))
                if key:
                    seen.add(key)
        counts.update(seen)
    return {k for k, n in counts.items() if n >= max(3, 0.5 * len(doc))}


def _is_noise_line(text: str, yc: float, page_height: float) -> bool:
    if re.fullmatch(r"[_\-−–\s]*", text):
        return True
    return yc > 0.82 * page_height and re.fullmatch(r"[\d\s_\-−–]*", text) is not None


def _parse_page(page, furniture) -> ParsedPage:
    words = [w[:5] for w in page.get_text("words")]
    blocks, tables = [], []
    table_rects = []
    for t in page.find_tables().tables:
        if t.row_count < 2 or t.col_count < 2:
            continue
        parsed = _build_table(t, words)
        tables.append(parsed)
        table_rects.append(pymupdf.Rect(t.bbox))
        blocks.append((t.bbox[1], parsed.markdown))

    free = [w for w in words if not any(_inside(w, r, 2) for r in table_rects)]
    lines = []
    for ln in _group_lines(free):
        text = _line_text(ln["w"])
        if _norm(text) in furniture or _is_noise_line(text, ln["yc"], page.rect.height):
            continue
        lines.append((ln, text))

    para, prev = [], None
    for ln, text in lines:
        if para and ln["yc"] - prev["yc"] > 1.9 * max(ln["h"], prev["h"]):
            blocks.append((para[0][0]["yc"], " ".join(t for _, t in para)))
            para = []
        para.append((ln, text))
        prev = ln
    if para:
        blocks.append((para[0][0]["yc"], " ".join(t for _, t in para)))

    blocks.sort(key=lambda b: b[0])
    return ParsedPage(number=page.number, markdown="\n\n".join(b[1] for b in blocks), tables=tables)


def parse_pdf(source: str | Path | bytes) -> list[ParsedPage]:
    """Parse every page of a PDF (path or raw bytes) into Markdown."""
    doc = pymupdf.open(stream=source, filetype="pdf") if isinstance(source, bytes) else pymupdf.open(source)
    with doc:
        furniture = _furniture(doc)
        return [_parse_page(page, furniture) for page in doc]


def pdf_to_markdown(source: str | Path | bytes) -> str:
    return "\n\n".join(p.markdown for p in parse_pdf(source) if p.markdown).strip()
