"""Tables cut by a page break get their column headers back.

When a table runs onto the next page, the rows there have no header row: the
parser finds no labels (flagged "N columns without header") or takes the first
data row for the header. If that table opens the page and the previous page ends
with a table of the same width, it is the same table: the previous page's header
is copied onto it. Each page stays readable on its own (the document viewer, and
search chunks that start on the second page), and the chunker joins the two
parts into one table when they share the header (src/section_chunker).

Runs on the indexing machine after the pages are parsed and pictures put in
place, so tables read from pictures are covered too. Free.
"""
from __future__ import annotations

import re

from src.picture_tables import _SEPARATOR, _cells, _is_row, _line, looks_like_data

MAX_TEXT_AROUND = 300  # page header/footer text allowed between the two parts
MIN_COLUMNS = 3        # a two-column table opening a page is usually a new label/value list


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*_#>\[\]]", " ", text or "")).strip()


def _opening_table(lines: list[str]) -> int | None:
    """Index of the header row of the table that opens the page (little text before it)."""
    for i in range(len(lines) - 1):
        if _is_row(lines[i]) and _SEPARATOR.match(lines[i + 1].strip()):
            return i if len(_plain("\n".join(lines[:i]))) <= MAX_TEXT_AROUND else None
    return None


def closing_header(markdown: str) -> str | None:
    """Header row of the table that closes the page (little text after it)."""
    lines = (markdown or "").splitlines()
    for i in range(len(lines) - 2, -1, -1):
        if _is_row(lines[i]) and _SEPARATOR.match(lines[i + 1].strip()) and (i == 0 or not _is_row(lines[i - 1])):
            end = i + 2
            while end < len(lines) and _is_row(lines[end]):
                end += 1
            if len(_plain("\n".join(lines[end:]))) > MAX_TEXT_AROUND:
                return None
            return lines[i].strip()
    return None


def carry_headers(pages) -> list[dict]:
    """Copy the header of a table that closes a page onto its headerless continuation
    at the top of the next page. `pages` are src.pdf_parser.ParsedPage (markdown is
    updated in place). Returns [{"page", "old_header", "first_row"}] for each table fixed."""
    fixed = []
    for prev, page in zip(pages, pages[1:]):
        lines = page.markdown.split("\n")
        i = _opening_table(lines)
        if i is None:
            continue
        header = _cells(lines[i])
        if len(header) < MIN_COLUMNS:
            continue                                    # two columns: usually its own label/value list
        empty = not any(header)
        if not empty and not looks_like_data(header):
            continue                                    # it has a real header of its own
        borrowed = closing_header(prev.markdown)
        if borrowed is None or len(_cells(borrowed)) != len(header) or not any(_cells(borrowed)):
            continue
        old, first_row = lines[i].strip(), (lines[i + 2].strip() if i + 2 < len(lines) else "")
        if empty:
            lines[i] = borrowed
        else:                                           # a data row taken for the header goes back to the rows
            lines[i:i + 2] = [borrowed, "|" + "---|" * len(header), _line(header)]
        page.markdown = "\n".join(lines)
        fixed.append({"page": page.number, "old_header": old, "first_row": first_row})
    return fixed


def unflag(tables, fixed_on_page: list[dict]) -> None:
    """Drop the "columns without header" problem of the tables that got a header back."""
    for table in tables:
        if not table.problems:
            continue
        for f in fixed_on_page:
            if f["old_header"] in table.markdown and f["first_row"] in table.markdown:
                table.problems = [p for p in table.problems if not p.endswith("columns without header")]
