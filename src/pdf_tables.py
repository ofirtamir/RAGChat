"""Tables of a PDF page -> Markdown, with validation. Used by src/pdf_parser.py."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.pdf_text import HEB, _inside, _words_to_text

NUMCELL = re.compile(r"^[\s\d.,\-−%$₪/X×()+]*\d[\s\d.,\-−%$₪/X×()+]*$")
MIN_COVERAGE = 0.97
MAX_COLUMNS = 20
MAX_HEADER_ROWS = 6


@dataclass
class ParsedTable:
    markdown: str
    problems: list[str] = field(default_factory=list)
    bbox: tuple[float, float, float, float] | None = None  # page coordinates, for re-reading the region
    numbers: list[str] = field(default_factory=list)       # numeric cell values, to check a re-read


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


def _data_row(row) -> bool:
    """A row of values: mostly numeric, with at least two numbers."""
    return _numeric_row(row) and sum(1 for x in row if x and _is_num(x)) >= 2


def _wide(row, spans) -> bool:
    """A single cell stretched over (nearly) the whole table: a title or a divider."""
    filled = [c for c, x in enumerate(row) if x]
    return len(filled) == 1 and spans is not None and len(row) > 2 and len(spans[filled[0]]) >= 0.8 * len(row)


def _fold_fragments(grid, spans) -> None:
    """PyMuPDF sometimes cuts one visual row into several: a cell that wraps to two lines
    ("מגורים" / "ד'") leaves a row holding just that one piece. Put each such piece back
    into the row of values it belongs to — the next one if its cell is empty there,
    otherwise the previous one."""
    def neighbour(i, step):
        j = i + step
        while 0 <= j < len(grid) and not any(grid[j]):
            j += step
        return j if 0 <= j < len(grid) else None

    for i, row in enumerate(grid):
        filled = [c for c, x in enumerate(row) if x]
        if len(filled) != 1 or _wide(row, spans[i]):
            continue
        c = filled[0]
        nxt, prv = neighbour(i, 1), neighbour(i, -1)
        if nxt is not None and _data_row(grid[nxt]) and not grid[nxt][c]                 and not (prv is not None and _data_row(grid[prv])):
            grid[nxt][c], row[c] = row[c], ""
        elif prv is not None and _data_row(grid[prv]) and len(row[c].split()) <= 3:
            grid[prv][c], row[c] = f"{grid[prv][c]} {row[c]}".strip(), ""


def _header_rows(grid, spans=None) -> tuple[list[int], int]:
    """Header = the rows above the longest block of rows of values. Empty rows and
    single-cell rows do not break the block; the header is every non-empty row above
    it, up to the previous row of values (or the top), without full-width title rows."""
    data = [_data_row(r) for r in grid]
    light = [sum(1 for x in r if x) <= 1 for r in grid]
    best_len, body_start, start, count = 0, -1, None, 0
    for i in range(len(grid) + 1):
        if i < len(grid) and data[i]:
            if start is None:
                start, count = i, 0
            count += 1
        elif i == len(grid) or not light[i]:
            if start is not None and count > best_len:
                best_len, body_start = count, start
            start = None
    if body_start < 0:
        return [0], 1
    hdr, j = [], body_start - 1
    while j >= 0 and not data[j]:
        if any(grid[j]) and not (spans and _wide(grid[j], spans[j])):
            hdr.insert(0, j)
        j -= 1
    if len(hdr) > MAX_HEADER_ROWS:      # that much text above the values is not a header
        hdr = hdr[-1:]
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

    _fold_fragments(grid, spans)
    hdr, _ = _header_rows(grid, spans)

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

    # No header found, but the first row has more text cells (labels) than the row
    # under it: it is the header.
    def text_cells(row):
        return sum(1 for v in row if v and not _is_num(v))

    if not any(header) and len(body) >= 2 and text_cells(body[0]) >= 2 \
            and text_cells(body[0]) > text_cells(body[1]):
        header, body = body[0], body[1:]

    problems = []
    if coverage < MIN_COVERAGE:
        problems.append(f"word coverage {coverage:.0%}")
    if not 2 <= len(cols) <= MAX_COLUMNS:
        problems.append(f"{len(cols)} columns")
    if not body:
        problems.append("no data rows")
    unlabeled = sum(1 for i, v in enumerate(header) if not v and any(row[i] for row in body))
    if unlabeled:
        problems.append(f"{unlabeled} columns without header")

    def md_row(values):
        return "| " + " | ".join(v.replace("|", "/") for v in values) + " |"

    md = f"**{' '.join(caption)}**\n\n" if caption else ""
    md += md_row(header) + "\n|" + "---|" * len(header) + "\n" + "\n".join(md_row(v) for v in body)
    numbers = [w[4] for w in in_table if re.search(r"\d", w[4])]
    return ParsedTable(markdown=md, problems=problems, bbox=tuple(table.bbox), numbers=numbers)
