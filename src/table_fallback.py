"""Re-read hard tables with a cheap Gemini model — only when the local parser failed.

Runs on the indexing machine (it renders PDF regions with PyMuPDF). The local
parser handles most tables; a table is sent to Gemini only when its validation
shows the local result is unusable:
- words inside the table were lost (coverage below the threshold), or
- the table has an implausible number of columns, or
- the header was read only in part (some columns labelled, others not) —
  a sign the cells were scrambled,
and the table is a real table (at least 2 rows of data with numbers).
Never sent, because a re-read buys nothing:
- "1 column" / "no data rows": text boxes the parser mistook for tables;
- a table with no header at all: usually the continuation of a table from the
  previous page (the chunker joins it to its header), or a list whose first
  row the parser already promoted to header.

Only the table's own region is sent, as an image, which keeps the request
small. The answer is accepted only if it is a Markdown table that contains at
least MIN_NUMBER_RECALL of the numbers the local parser saw in that region —
otherwise the local version is kept. Every call's cost is counted.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

from config import MODEL_PRICES

logger = logging.getLogger(__name__)

MODEL = os.getenv("TABLE_FALLBACK_MODEL", "gemini-3.1-flash-lite")
MAX_TABLES_PER_DOCUMENT = int(os.getenv("TABLE_FALLBACK_MAX_PER_DOC", "5"))
MIN_NUMBER_RECALL = 0.9
RENDER_ZOOM = 2.0
_HARD = re.compile(r"word coverage|^\d+ columns$")
_UNLABELED = re.compile(r"^(\d+) columns without header$")
_NOT_A_TABLE = re.compile(r"^1 columns$|^no data rows$")

PROMPT = """המר את הטבלה שבתמונה לטבלת Markdown אחת, נאמנה למקור, בעברית.
כללים:
- העתק טקסט ומספרים בדיוק כפי שהם. אל תסכם, אל תחשב, אל תתקן ואל תוסיף דבר.
- העמודה הימנית ביותר בתמונה היא העמודה הראשונה בטבלה.
- שורת הכותרת היא השורה הראשונה. אם לכותרת יש שתי רמות, חבר אותן כך: "עליונה - תחתונה".
- תא ממוזג נכתב פעם אחת; תאים ריקים נשארים ריקים.
- החזר את טבלת ה-Markdown בלבד, בלי הסברים ובלי גדר קוד."""


@dataclass
class Repair:
    markdown: str
    cost_usd: float
    input_tokens: int
    output_tokens: int


def is_hard(table) -> bool:
    """True when the local parse of this table is unusable and worth a paid re-read."""
    problems = table.problems or []
    if not problems or any(_NOT_A_TABLE.match(p) for p in problems):
        return False
    header = next((ln for ln in table.markdown.splitlines() if ln.startswith("|")), "")
    labels = [c.strip() for c in header.strip().strip("|").split("|")]
    partial_header = any(_UNLABELED.match(p) for p in problems) and any(labels)
    if not partial_header and not any(_HARD.search(p) for p in problems):
        return False
    rows = [ln for ln in table.markdown.splitlines() if ln.startswith("|")][2:]
    return len(rows) >= 2 and len(table.numbers) >= 2 and table.bbox is not None


def _digits(value: str) -> str:
    return re.sub(r"[^\d.]", "", value).strip(".")


def number_recall(markdown: str, numbers: list[str]) -> float:
    """Share of the locally seen numbers that appear in `markdown` (formatting ignored)."""
    wanted = [d for d in (_digits(n) for n in numbers) if len(d) >= 2]
    if not wanted:
        return 1.0
    found = {_digits(t) for t in re.findall(r"[\d][\d,.\-/]*", markdown)}
    found |= {d.replace(".", "") for d in found}
    return sum(1 for w in wanted if w in found or w.replace(".", "") in found) / len(wanted)


def _clean(text: str) -> str:
    text = re.sub(r"^```\w*\n|\n```$", "", (text or "").strip())
    lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("|")]
    return "\n".join(lines)


def replace_table(table_markdown: str, new_table: str) -> str:
    """Swap the table lines of a parsed table for `new_table`, keeping its caption line."""
    caption = "" if table_markdown.startswith("|") else table_markdown.split("\n\n| ", 1)[0]
    return f"{caption}\n\n{new_table}" if caption else new_table


def _price(model: str) -> tuple[float, float]:
    best = max((m for m in MODEL_PRICES if model.startswith(m)), key=len, default=None)
    return MODEL_PRICES[best] if best else (0.0, 0.0)


def reread_table(page, table, client=None) -> Repair:
    """Ask Gemini to read one table region. `page` is a PyMuPDF page, `table` a
    src.pdf_parser.ParsedTable. The call is paid even when its answer is rejected,
    so a Repair always comes back; its markdown is empty when the answer can't be
    trusted (not a table, or missing the numbers the local parser saw)."""
    import pymupdf
    from google.genai import types

    if client is None:
        from src.embeddings import genai_client
        client = genai_client()

    x0, y0, x1, y1 = table.bbox
    clip = pymupdf.Rect(x0 - 4, y0 - 4, x1 + 4, y1 + 4) & page.rect
    png = page.get_pixmap(matrix=pymupdf.Matrix(RENDER_ZOOM, RENDER_ZOOM), clip=clip).tobytes("png")
    resp = client.models.generate_content(
        model=MODEL,
        contents=[types.Part.from_bytes(data=png, mime_type="image/png"), PROMPT],
        config=types.GenerateContentConfig(
            temperature=0, thinking_config=types.ThinkingConfig(thinking_level="minimal")),
    )
    usage = resp.usage_metadata
    inp = usage.prompt_token_count or 0
    out = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
    price_in, price_out = _price(MODEL)
    cost = (inp * price_in + out * price_out) / 1_000_000
    markdown = _clean(resp.text)
    rows = markdown.splitlines()
    ok = len(rows) >= 3 and set(rows[1].replace("|", "").strip()) <= set("-: ")
    recall = number_recall(markdown, table.numbers) if ok else 0.0
    if not ok or recall < MIN_NUMBER_RECALL:
        logger.info("Gemini table rejected (table=%s, numbers recall %.0f%%)", ok, recall * 100)
        return Repair(markdown="", cost_usd=cost, input_tokens=inp, output_tokens=out)
    return Repair(markdown=markdown, cost_usd=cost, input_tokens=inp, output_tokens=out)
