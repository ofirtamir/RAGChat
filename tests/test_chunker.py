"""Unit tests for table-aware chunking in src/chunker.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from langchain_core.documents import Document  # noqa: E402

from config import TABLE_CHUNK_MAX, TABLE_CONTEXT_CHARS  # noqa: E402
from src.chunker import CONTINUED, chunk_documents, create_splitter, split_text  # noqa: E402

HEADER = "| קומה | שטח | סה\"כ |\n|---|---|---|"


def table(n_rows: int) -> str:
    rows = "\n".join(f"| קומה {i} | {100 + i}.50 | {200 + i}.75 |" for i in range(n_rows))
    return f"{HEADER}\n{rows}"


def test_plain_text_chunks_like_before():
    text = "\n\n".join(f"פסקה מספר {i} " + "מילה " * 60 for i in range(12))
    assert split_text(text) == create_splitter().split_text(text)


def test_small_table_kept_whole_with_caption_and_context():
    text = f"להלן תחשיב ההשבחה:\n\n**מצב קודם**\n\n{table(5)}\n\nטקסט אחרי הטבלה."
    chunks = split_text(text)
    table_chunks = [c for c in chunks if "|---|" in c]
    assert len(table_chunks) == 1
    tc = table_chunks[0]
    assert tc.startswith("להלן תחשיב ההשבחה:\n\n**מצב קודם**\n\n| קומה")
    assert all(f"| קומה {i} |" in tc for i in range(5))
    assert any(c == "טקסט אחרי הטבלה." for c in chunks)


def test_long_table_split_by_rows_with_header_repeated():
    n = 150
    chunks = split_text(f"**מצב חדש**\n\n{table(n)}")
    assert len(chunks) > 1
    for i, c in enumerate(chunks):
        assert len(c) <= TABLE_CHUNK_MAX
        assert HEADER in c
        assert ("**מצב חדש**" in c) and ((CONTINUED in c) == (i > 0))
    header_lines = HEADER.splitlines()
    rows = [ln for c in chunks for ln in c.splitlines() if ln.startswith("| קומה ") and ln not in header_lines]
    assert rows == [f"| קומה {i} | {100 + i}.50 | {200 + i}.75 |" for i in range(n)]


def test_long_context_paragraph_is_trimmed():
    intro = "מבוא " * 200
    chunks = split_text(f"{intro.strip()}\n\n{table(3)}")
    tc = next(c for c in chunks if "|---|" in c)
    context = tc.split("\n\n| קומה")[0]
    assert context.startswith("…")
    assert len(context) <= TABLE_CONTEXT_CHARS + 1


def test_metadata_and_page_kept_on_every_chunk():
    docs = [Document(page_content=f"טקסט\n\n{table(3)}", metadata={"source": "a.pdf", "page": 4})]
    chunks = chunk_documents(docs)
    assert chunks and all(c.metadata == {"source": "a.pdf", "page": 4} for c in chunks)
