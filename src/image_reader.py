"""Read tables and text that were pasted into the decisions as pictures.

Runs on the indexing machine only (it renders PDF regions with PyMuPDF). Many
decisions paste tables (comparable sales, levy calculations) and quoted
paragraphs as screenshots, which have no text layer, so the local parser sees
nothing there. For each candidate picture the parser found (big enough, shown
once in the document, no text drawn over it):

1. A free local test keeps only "document" pictures — mostly white with dark,
   uncoloured ink, like a table or a paragraph. Maps, aerial photos, plans and
   photographs are colourful or dense and are skipped without any call.
2. A document picture is sent, alone, to a cheap Gemini model, which returns
   a Markdown table, the text as written, or NONE (a picture after all).
3. Every answer — NONE included — is cached on disk by the image's hash, so
   the same picture (re-parsing, the same table in several decisions) is never
   paid for twice.

There is no text layer to check the answer against, so what Gemini read is
labelled in the Markdown as read from an image.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from dataclasses import dataclass

from config import DATA_DIR, MODEL_PRICES

logger = logging.getLogger(__name__)

MODEL = os.getenv("IMAGE_READER_MODEL", os.getenv("TABLE_FALLBACK_MODEL", "gemini-3.1-flash-lite"))
MAX_IMAGES_PER_DOCUMENT = int(os.getenv("IMAGE_READER_MAX_PER_DOC", "60"))
RENDER_ZOOM = 2.0
MAX_OUTPUT_TOKENS = 4096
MIN_WHITE = 0.4         # share of near-white pixels in a document picture
MAX_COLOUR = 20.0       # mean (max-min) channel spread; photos and maps are far above this
LABEL = "*[מתוך תמונה במסמך]*"
CACHE_PATH = DATA_DIR / "image_reads.db"

PROMPT = """בתמונה קטע מתוך החלטה של שמאי מכריע. החזר את התוכן שלה כטקסט, נאמן למקור, בעברית.
- אם יש בה טבלה: החזר טבלת Markdown. העמודה הימנית ביותר בתמונה היא העמודה הראשונה. \
שורת הכותרת היא השורה הראשונה; כותרת בשתי רמות נכתבת "עליונה - תחתונה". תא ממוזג נכתב פעם אחת.
- אם יש בה טקסט רגיל: העתק אותו כלשונו, בפסקאות.
- העתק מספרים בדיוק כפי שהם. אל תסכם, אל תחשב, אל תתקן ואל תוסיף דבר.
- אם זו מפה, תצלום, תשריט, תוכנית אדריכלית, חתימה או לוגו בלי נתונים לקריאה: החזר בדיוק NONE.
החזר את התוכן בלבד, בלי הסברים ובלי גדר קוד."""


@dataclass
class Reading:
    text: str           # Markdown to put in place of the picture; "" = nothing to add
    cost_usd: float


# ---------- free local test ----------

def looks_like_document(page, rect) -> bool:
    """Mostly white with uncoloured ink: a table or a paragraph, not a map or a photo."""
    import numpy as np
    import pymupdf

    pix = page.get_pixmap(matrix=pymupdf.Matrix(0.5, 0.5), clip=rect, colorspace=pymupdf.csRGB)
    if pix.width < 4 or pix.height < 4:
        return False
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).astype(np.int16)
    white = float((rgb.min(axis=2) > 200).mean())
    colour = float((rgb.max(axis=2) - rgb.min(axis=2)).mean())
    # thin strokes of an old scan turn light grey at this size, so "not blank" is
    # measured as non-white pixels rather than dark ones
    return MIN_WHITE <= white <= 0.99 and colour < MAX_COLOUR


# ---------- cache ----------

_lock = threading.Lock()


def _cache() -> sqlite3.Connection:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CACHE_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS reads (digest TEXT, model TEXT, text TEXT, "
                 "PRIMARY KEY (digest, model))")
    return conn


def cached(digest: str) -> str | None:
    with _lock, _cache() as conn:
        row = conn.execute("SELECT text FROM reads WHERE digest = ? AND model = ?", (digest, MODEL)).fetchone()
    return row[0] if row else None


def remember(digest: str, text: str) -> None:
    with _lock, _cache() as conn:
        conn.execute("INSERT OR REPLACE INTO reads VALUES (?, ?, ?)", (digest, MODEL, text))


# ---------- the paid call ----------

def _price(model: str) -> tuple[float, float]:
    best = max((m for m in MODEL_PRICES if model.startswith(m)), key=len, default=None)
    return MODEL_PRICES[best] if best else (0.0, 0.0)


def clean_answer(answer: str) -> str:
    """Gemini's answer -> the Markdown to insert ("" when there is nothing to read)."""
    text = re.sub(r"^```\w*\n|\n?```$", "", (answer or "").strip()).strip()
    if not text or text.upper().strip(" .") == "NONE":
        return ""
    if not re.search(r"[א-ת\d]", text):
        return ""
    return f"{LABEL}\n\n{text}"


def read_image(page, image, client=None) -> Reading:
    """One paid call: turn a document picture into Markdown and cache the answer.
    `page` is a PyMuPDF page, `image` a src.pdf_parser.ParsedImage. The caller
    checks the cache and `looks_like_document` first. Raises when the call fails."""
    import pymupdf
    from google.genai import types

    rect = pymupdf.Rect(image.bbox)
    if client is None:
        from src.embeddings import genai_client
        client = genai_client()

    png = page.get_pixmap(matrix=pymupdf.Matrix(RENDER_ZOOM, RENDER_ZOOM), clip=rect).tobytes("png")
    resp = client.models.generate_content(
        model=MODEL,
        contents=[types.Part.from_bytes(data=png, mime_type="image/png"), PROMPT],
        config=types.GenerateContentConfig(
            temperature=0, max_output_tokens=MAX_OUTPUT_TOKENS,
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
            thinking_config=types.ThinkingConfig(thinking_level="minimal")),
    )
    usage = resp.usage_metadata
    inp = usage.prompt_token_count or 0
    out = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
    price_in, price_out = _price(MODEL)
    cost = (inp * price_in + out * price_out) / 1_000_000
    finish = str(getattr(resp.candidates[0], "finish_reason", "") if resp.candidates else "")
    # a reply cut off at the token limit is usually a repetition loop: don't keep it
    text = "" if "MAX_TOKENS" in finish else clean_answer(resp.text)
    remember(image.digest, text)
    return Reading(text=text, cost_usd=cost)
