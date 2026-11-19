"""Tidy the Markdown read from a pictured table before it goes into the document.

The model's reading of a picture (src/image_reader.py) is cached as returned;
these fixes run every time it is put in place of the picture, so a re-parse
repairs documents for free:

- a table with no header row (the picture continues a table from the previous
  picture or page) gets the previous table's header when the columns match, or
  an empty header: without the "|---|" line, Markdown shows the rows as one
  run-on paragraph and the chunker does not see a table;
- the header and every row are padded to the widest row, so no cell is dropped
  when the model reads one column more in the rows than in the header;
- "מייר" after a number is the model's reading of מ"ר (square metres).
"""
from __future__ import annotations

import re

_SEPARATOR = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_SQM = re.compile(r"(\d)\s*מייר")


def _is_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.endswith("|") and len(s) > 1


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip()[1:-1].split("|")]


def _line(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _padded(cells: list[str], width: int) -> list[str]:
    return cells + [""] * (width - len(cells))


def last_header(markdown: str) -> str | None:
    """The header row of the last table (with a separator line) in this Markdown."""
    lines = (markdown or "").splitlines()
    for i in range(len(lines) - 2, -1, -1):
        if _is_row(lines[i]) and _SEPARATOR.match(lines[i + 1].strip()) and (i == 0 or not _is_row(lines[i - 1])):
            return lines[i].strip()
    return None


def _tidy_table(block: list[str], hint: str | None) -> list[str]:
    has_header = len(block) >= 2 and bool(_SEPARATOR.match(block[1].strip()))
    rows = [_cells(r) for r in (block[2:] if has_header else block) if not _SEPARATOR.match(r.strip())]
    header = _cells(block[0]) if has_header else None
    width = max([len(r) for r in rows] + ([len(header)] if header else []))
    if header is None:
        borrowed = _cells(hint) if hint else []
        header = borrowed if len(borrowed) == width else [""] * width
    return ([_line(_padded(header, width)), "|" + "---|" * width]
            + [_line(_padded(r, width)) for r in rows])


def tidy(text: str, hint: str | None = None) -> str:
    """The picture's Markdown with every table well formed. `hint` is the header row
    of the table before the picture (last_header of the text preceding it)."""
    if not text:
        return text
    lines, out, i = _SQM.sub(r'\1 מ"ר', text).splitlines(), [], 0
    while i < len(lines):
        if not _is_row(lines[i]) or _SEPARATOR.match(lines[i].strip()):
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < len(lines) and (_is_row(lines[j]) or _SEPARATOR.match(lines[j].strip())):
            j += 1
        out += _tidy_table(lines[i:j], hint)
        hint = last_header("\n".join(out))
        i = j
    return "\n".join(out)
