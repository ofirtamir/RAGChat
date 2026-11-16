import logging
import math
from functools import lru_cache

import requests
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from config import EMBEDDING_DIM, EMBEDDING_MODEL, GOOGLE_API_KEY

logger = logging.getLogger(__name__)

_COUNT_URL = "https://generativelanguage.googleapis.com/v1beta/{model}:countTokens"
_COUNT_BATCH = 50
_EMBED_BATCH = 100  # texts per embedContent request
ESTIMATED_CHARS_PER_TOKEN = 1.6  # measured on the decisions corpus; used only if counting fails

MODEL_NAME = EMBEDDING_MODEL if EMBEDDING_MODEL.startswith("models/") else f"models/{EMBEDDING_MODEL}"


def get_embedding_model() -> GoogleGenerativeAIEmbeddings:
    """LangChain embeddings for the uploads store (Chroma, full-size vectors)."""
    return GoogleGenerativeAIEmbeddings(
        model=EMBEDDING_MODEL,
        google_api_key=GOOGLE_API_KEY,
    )


@lru_cache(maxsize=1)
def genai_client():
    from google import genai

    return genai.Client(api_key=GOOGLE_API_KEY)


def normalize(vector: list[float]) -> list[float]:
    """Unit length. Gemini only normalises full-size vectors; shortened ones need it for cosine."""
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


def embed_config(task_type: str):
    from google.genai import types

    return types.EmbedContentConfig(task_type=task_type, output_dimensionality=EMBEDDING_DIM)


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Library-index vectors for document chunks (online, full price)."""
    vectors: list[list[float]] = []
    client = genai_client()
    for start in range(0, len(texts), _EMBED_BATCH):
        resp = client.models.embed_content(
            model=MODEL_NAME, contents=texts[start:start + _EMBED_BATCH],
            config=embed_config("RETRIEVAL_DOCUMENT"),
        )
        vectors.extend(normalize(list(e.values)) for e in resp.embeddings)
    return vectors


@lru_cache(maxsize=512)
def _embed_query_cached(text: str) -> tuple[float, ...]:
    resp = genai_client().models.embed_content(
        model=MODEL_NAME, contents=[text], config=embed_config("RETRIEVAL_QUERY"),
    )
    return tuple(normalize(list(resp.embeddings[0].values)))


def embed_query(text: str) -> list[float]:
    """Library-index vector for a search query (cached: repeated questions cost nothing)."""
    return list(_embed_query_cached(text.strip()))


def count_embedding_tokens(texts: list[str]) -> tuple[int, bool]:
    """Exact number of tokens the embedding model bills for `texts`.

    Uses Gemini's countTokens endpoint, which is free. Several texts go in one
    request as separate contents; the total equals the sum of counting each
    alone. Returns (tokens, exact); falls back to a character-based estimate
    (exact=False) if the endpoint can't be reached.
    """
    total = 0
    try:
        for start in range(0, len(texts), _COUNT_BATCH):
            batch = texts[start:start + _COUNT_BATCH]
            resp = requests.post(
                _COUNT_URL.format(model=MODEL_NAME),
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
