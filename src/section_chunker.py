"""Section-aware chunking of a whole parsed decision.

The parser emits Markdown per page. Chunking page by page cuts a paragraph or
a table that runs over a page break, so the pages are joined first:
- a paragraph that ends a page mid-sentence continues on the next page;
- a table that ends a page and a table that starts the next one with the same
  column count are one table (a repeated header row is dropped).

Decisions are organised in numbered sections ("1. מטרת ההחלטה", "9.4 ...").
Chunks never span two top-level sections, carry their section heading, and
are packed up to SECTION_CHUNK_SIZE characters. Tables stay whole (or are split
by rows with the header repeated, see src/chunker) together with the caption
and the short lines that introduce them (moved into the table's chunk, up to
TABLE_CONTEXT_CHARS), or the end of a longer introducing paragraph (copied).
Consecutive prose chunks of
the same section overlap by the last sentence of the previous chunk, at most
SECTION_OVERLAP characters.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from config import TABLE_CONTEXT_CHARS
from src.chunker import _context_tail, _is_caption, _is_table, _table_chunks

SECTION_CHUNK_SIZE = 1000
SECTION_OVERLAP = 150
_SECTION_RE = re.compile(r"^(\d{1,2})\.(?:\d{1,2}\.?)*\s+\S")
_HEADING_WORDS = 5
_SENTENCE_END = re.compile(r"[.!?:;״\"')\]]\s*$")


@dataclass
class Block:
    text: str
    page: int
    kind: str  # "prose" | "table" | "caption"


@dataclass
class Chunk:
    text: str
    page: int
    section: str


def _blocks(pages: list[tuple[int, str]]) -> list[Block]:
    out: list[Block] = []
    for number, markdown in pages:
        page_blocks = [b.strip() for b in re.split(r"\n\s*\n", markdown or "") if b.strip()]
        for i, text in enumerate(page_blocks):
            kind = "table" if _is_table(text) else "caption" if _is_caption(text) else "prose"
            first_on_page = i == 0
            prev = out[-1] if out else None
            if first_on_page and prev is not None and prev.page != number:
                if kind == "table" and prev.kind == "table" and _same_columns(prev.text, text):
                    prev.text = _join_tables(prev.text, text)
                    continue
                if kind == "prose" and prev.kind == "prose" and not _SENTENCE_END.search(prev.text):
                    prev.text = f"{prev.text} {text}"
                    continue
            out.append(Block(text=text, page=number, kind=kind))
    return out


def _columns(table: str) -> int:
    first = table.strip().splitlines()[0]
    return first.strip().strip("|").count("|") + 1


def _same_columns(a: str, b: str) -> bool:
    return _columns(a) == _columns(b)


def _join_tables(first: str, second: str) -> str:
    a = [ln for ln in first.splitlines() if ln.strip()]
    b = [ln for ln in second.splitlines() if ln.strip()]
    rows = b[2:] if b[0].strip() == a[0].strip() else [b[0]] + b[2:]
    return "\n".join(a + rows)


def _section_of(text: str) -> tuple[int, str] | None:
    """(number, short heading) when a prose block starts with a numbered section ("3. ...")."""
    m = _SECTION_RE.match(text)
    if not m:
        return None
    rest = text[m.end() - 1:]
    heading = re.split(r"\s\d{1,2}\.\d{1,2}\.?\s|[.:,;(]", rest, maxsplit=1)[0]
    return int(m.group(1)), " ".join(heading.split()[:_HEADING_WORDS])


def _starts_new_section(number: int, current: int | None) -> bool:
    """Section numbers only move forward ("2.1" stays in 2; a stray "2." after 3 is not a section)."""
    if current is None:
        return number <= 3
    return current < number <= current + 3


def _last_sentence(text: str) -> str:
    tail = text[-SECTION_OVERLAP:]
    cut = max(tail.rfind(". "), tail.rfind(": "))
    return tail[cut + 2:] if cut != -1 else ""


def _split_long(text: str) -> list[str]:
    """Split one long paragraph at sentence boundaries into <= SECTION_CHUNK_SIZE pieces."""
    if len(text) <= SECTION_CHUNK_SIZE:
        return [text]
    sentences = re.split(r"(?<=[.!?:])\s+", text)
    parts, current = [], ""
    for s in sentences:
        while len(s) > SECTION_CHUNK_SIZE:
            if current:
                parts.append(current)
                current = ""
            cut = s.rfind(" ", 0, SECTION_CHUNK_SIZE)
            cut = cut if cut > 0 else SECTION_CHUNK_SIZE
            parts.append(s[:cut])
            s = s[cut:].lstrip()
        if current and len(current) + 1 + len(s) > SECTION_CHUNK_SIZE:
            parts.append(current)
            current = s
        else:
            current = f"{current} {s}".strip()
    if current:
        parts.append(current)
    return parts


def _lead_in(buf: list[str]) -> str:
    """Move the short paragraphs that lead into a table ("להלן המועד הקובע:",
    sub-headings) out of the prose buffer, so they go with the table instead of
    being left behind as a tiny chunk of their own. Empties `buf` when only the
    overlap from the previous chunk would be left."""
    lead: list[str] = []
    while buf and not buf[-1].startswith("…"):
        if sum(len(p) + 2 for p in lead) + len(buf[-1]) > TABLE_CONTEXT_CHARS:
            break
        lead.insert(0, buf.pop())
    if lead and all(p.startswith("…") for p in buf):
        buf.clear()
    return "\n\n".join(lead)


def chunk_parsed_document(pages: list[tuple[int, str]]) -> list[Chunk]:
    """pages: [(page_number, markdown)] in order. Returns chunks with page and section."""
    blocks = _blocks(pages)
    chunks: list[Chunk] = []
    section, section_number = "", None
    buf: list[str] = []
    buf_page: int | None = None
    last_prose = ""

    def label(text: str) -> str:
        return f"[{section}]\n{text}" if section else text

    def flush() -> None:
        nonlocal buf, buf_page
        if buf:
            chunks.append(Chunk(label("\n\n".join(buf)), buf_page, section))
        buf, buf_page = [], None

    i = 0
    while i < len(blocks):
        block = blocks[i]
        if block.kind == "prose":
            found = _section_of(block.text)
            if found and _starts_new_section(found[0], section_number):
                section_number = found[0]
                flush()
                section = f"סעיף {found[0]}: {found[1]}" if found[1] else f"סעיף {found[0]}"
                last_prose = ""
            for piece in _split_long(block.text):
                size = sum(len(b) + 2 for b in buf) + len(piece)
                if buf and size > SECTION_CHUNK_SIZE:
                    flush()
                    overlap = _last_sentence(last_prose)
                    if overlap:
                        buf.append("…" + overlap)
                if buf_page is None:
                    buf_page = block.page
                buf.append(piece)
                last_prose = piece
            i += 1
            continue

        caption = ""
        if block.kind == "caption":
            if i + 1 < len(blocks) and blocks[i + 1].kind == "table":
                caption, block = block.text, blocks[i + 1]
                i += 1
            else:  # a bold line that isn't a table caption: treat as prose
                blocks[i] = Block(block.text, block.page, "prose")
                continue
        context = _lead_in(buf) or (_context_tail(last_prose) if last_prose else "")
        flush()
        for part in _table_chunks(block.text, caption, context):
            chunks.append(Chunk(label(part), block.page, section))
        i += 1
    flush()
    return chunks
