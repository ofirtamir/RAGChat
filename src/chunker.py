"""Chunking that keeps Markdown tables intact.

Prose goes through the usual recursive splitter. A table (optionally with the
bold caption line src/pdf_parser emits above it) becomes its own chunk,
prefixed with the end of the paragraph that introduces it so the numbers keep
their meaning. Tables longer than TABLE_CHUNK_MAX are split by rows and every
part repeats the caption and header row. Plain text with no tables chunks
exactly as before.
"""
import re

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import CHUNK_OVERLAP, CHUNK_SIZE, TABLE_CHUNK_MAX, TABLE_CONTEXT_CHARS

CONTINUED = "(המשך טבלה)"


def create_splitter() -> RecursiveCharacterTextSplitter:
    """Create a text splitter with configured chunk size and overlap."""
    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
        length_function=len,
    )


def _is_table(block: str) -> bool:
    lines = [ln for ln in block.splitlines() if ln.strip()]
    return len(lines) >= 2 and all(ln.lstrip().startswith("|") for ln in lines)


def _is_caption(block: str) -> bool:
    return bool(re.fullmatch(r"\*\*[^\n]+\*\*", block.strip()))


def _context_tail(paragraph: str) -> str:
    if len(paragraph) <= TABLE_CONTEXT_CHARS:
        return paragraph
    tail = paragraph[-TABLE_CONTEXT_CHARS:]
    cut = tail.find(" ")
    return "…" + (tail[cut + 1:] if cut != -1 else tail)


def _table_chunks(table: str, caption: str, context: str) -> list[str]:
    lines = [ln for ln in table.splitlines() if ln.strip()]
    header, rows = lines[:2], lines[2:]

    def render(part_rows: list[str], continued: bool) -> str:
        cap = caption + (" " + CONTINUED if continued else "") if caption else (CONTINUED if continued else "")
        pieces = [p for p in (context, cap) if p]
        return "\n\n".join(pieces + ["\n".join(header + part_rows)])

    if len(render(rows, False)) <= TABLE_CHUNK_MAX:
        return [render(rows, False)]

    parts, current = [], []
    for row in rows:
        if current and len(render(current + [row], bool(parts))) > TABLE_CHUNK_MAX:
            parts.append(render(current, bool(parts)))
            current = []
        current.append(row)
    if current:
        parts.append(render(current, bool(parts)))
    return parts


def split_text(text: str, splitter: RecursiveCharacterTextSplitter | None = None) -> list[str]:
    """Split one document's text into chunk strings, keeping tables whole."""
    splitter = splitter or create_splitter()
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if not any(_is_table(b) for b in blocks):
        return splitter.split_text(text)

    chunks, prose = [], []

    def flush_prose():
        if prose:
            chunks.extend(splitter.split_text("\n\n".join(prose)))
            prose.clear()

    i = 0
    while i < len(blocks):
        block = blocks[i]
        caption = ""
        if _is_caption(block) and i + 1 < len(blocks) and _is_table(blocks[i + 1]):
            caption, block = block, blocks[i + 1]
            i += 1
        if _is_table(block):
            context = _context_tail(prose[-1]) if prose else ""
            flush_prose()
            chunks.extend(_table_chunks(block, caption, context))
        else:
            prose.append(block)
        i += 1
    flush_prose()
    return chunks


def chunk_documents(documents: list[Document]) -> list[Document]:
    """Split documents into chunks for embedding; each chunk keeps its document's metadata."""
    splitter = create_splitter()
    return [
        Document(page_content=chunk, metadata=dict(doc.metadata))
        for doc in documents
        for chunk in split_text(doc.page_content, splitter)
    ]
