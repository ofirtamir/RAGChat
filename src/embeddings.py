import logging

import requests
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from config import GOOGLE_API_KEY, EMBEDDING_MODEL

logger = logging.getLogger(__name__)

_COUNT_URL = "https://generativelanguage.googleapis.com/v1beta/{model}:countTokens"
_COUNT_BATCH = 50
ESTIMATED_CHARS_PER_TOKEN = 1.6  # measured on the decisions corpus; used only if counting fails


def get_embedding_model() -> GoogleGenerativeAIEmbeddings:
    """Return the configured Google embedding model."""
    return GoogleGenerativeAIEmbeddings(
        model=EMBEDDING_MODEL,
        google_api_key=GOOGLE_API_KEY,
    )


def count_embedding_tokens(texts: list[str]) -> tuple[int, bool]:
    """Exact number of tokens the embedding model bills for `texts`.

    Uses Gemini's countTokens endpoint, which is free. Several texts go in one
    request as separate contents; the total equals the sum of counting each
    alone. Returns (tokens, exact); falls back to a character-based estimate
    (exact=False) if the endpoint can't be reached.
    """
    model = EMBEDDING_MODEL if EMBEDDING_MODEL.startswith("models/") else f"models/{EMBEDDING_MODEL}"
    total = 0
    try:
        for start in range(0, len(texts), _COUNT_BATCH):
            batch = texts[start:start + _COUNT_BATCH]
            resp = requests.post(
                _COUNT_URL.format(model=model),
                params={"key": GOOGLE_API_KEY},
                json={"contents": [{"parts": [{"text": t}]} for t in batch]},
                timeout=30,
            )
            resp.raise_for_status()
            total += int(resp.json()["totalTokens"])
        return total, True
    except Exception as exc:
        logger.warning("countTokens failed, estimating embedding tokens: %s", exc)
        return round(sum(len(t) for t in texts) / ESTIMATED_CHARS_PER_TOKEN), False
