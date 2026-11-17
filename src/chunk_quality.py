"""Quality checks for the chunks of one document, shown in the admin chunk viewer.

Each chunk gets flags that point at likely problems:
- tiny        so little text that a search hit on it tells the answer model nothing
              (small tables are not flagged: a whole table is a complete unit)
- long        well over the target size (usually a big table kept whole)
- no_section  not under a numbered section, so it carries no section heading
- garbled     text that looks mis-decoded (replacement characters, Hebrew read as Latin-1)
- duplicate   the same text appears in another chunk of the document
- table       holds a Markdown table (not a problem, shown for orientation)

Token counts are estimates (Hebrew ≈ 1.6 characters per token for Gemini).
"""
from __future__ import annotations

import re

from src.section_chunker import SECTION_CHUNK_SIZE

TINY_CHARS = 150
LONG_CHARS = int(SECTION_CHUNK_SIZE * 2)
CHARS_PER_TOKEN = 1.6
PROBLEM_FLAGS = ("tiny", "long", "no_section", "garbled", "duplicate")

_LABEL_RE = re.compile(r"^\[[^\]\n]{1,120}\]\n")
_TABLE_RE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_LATIN1_HEBREW = re.compile(r"[à-ú]")  # à..ú = Windows-1255 Hebrew read as Latin-1


def body(text: str) -> str:
    """Chunk text without its "[section]" label line."""
    return _LABEL_RE.sub("", text or "", count=1)


def flags_for(text: str, section: str, seen: dict[str, int]) -> list[str]:
    content = body(text).strip()
    flags = []
    is_table = bool(_TABLE_RE.search(content))
    if len(content) < TINY_CHARS and not is_table:  # a small whole table is a complete unit
        flags.append("tiny")
    if len(text) > LONG_CHARS:
        flags.append("long")
    if not section:
        flags.append("no_section")
    if "�" in content or len(_LATIN1_HEBREW.findall(content)) >= 5:
        flags.append("garbled")
    if seen.get(content, 0) > 1:
        flags.append("duplicate")
    if is_table:
        flags.append("table")
    return flags


def analyse(chunks: list[dict]) -> tuple[list[dict], dict]:
    """chunks: [{text, section, page, ...}] in order. Returns the chunks with
    `chars`, `tokens_est` and `flags` added, and a summary for the document."""
    seen: dict[str, int] = {}
    for c in chunks:
        key = body(c["text"]).strip()
        seen[key] = seen.get(key, 0) + 1
    out = []
    for i, c in enumerate(chunks):
        chars = len(c["text"] or "")
        out.append({**c, "chunk_index": c.get("chunk_index", i), "chars": chars,
                    "tokens_est": round(chars / CHARS_PER_TOKEN),
                    "flags": flags_for(c["text"], c.get("section") or "", seen)})
    return out, summarise(out)


def summarise(chunks: list[dict]) -> dict:
    sizes = sorted(c["chars"] for c in chunks)
    counts = {f: sum(1 for c in chunks if f in c["flags"]) for f in (*PROBLEM_FLAGS, "table")}
    buckets = [0] * 6  # <250, <500, <750, <1000, <1500, 1500+
    for n in sizes:
        buckets[next((i for i, edge in enumerate((250, 500, 750, 1000, 1500)) if n < edge), 5)] += 1
    return {
        "chunks": len(chunks),
        "chars": sum(sizes),
        "tokens_est": round(sum(sizes) / CHARS_PER_TOKEN),
        "min": sizes[0] if sizes else 0,
        "median": sizes[len(sizes) // 2] if sizes else 0,
        "max": sizes[-1] if sizes else 0,
        "sections": len({c.get("section") for c in chunks if c.get("section")}),
        "flags": counts,
        "with_problems": sum(1 for c in chunks if any(f in PROBLEM_FLAGS for f in c["flags"])),
        "size_buckets": buckets,
    }
