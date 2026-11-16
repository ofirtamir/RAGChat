"""Gemini chat model factories, one per job, so each job runs on the cheapest model that does it well.

- answer_llm(): writes the answer the user reads (LLM_MODEL).
- fast_llm():   small JSON decisions — routing/rewriting the query, deciding
                whether a whole document is needed, reranking (LLM_MODEL_FAST).

Thinking tokens are billed as output, the most expensive token type, so
thinking is kept low for answers and switched off for the small decisions.
Gemini 3+ models take `thinking_level`; 2.5 models take `thinking_budget`.
"""
from __future__ import annotations

from langchain_google_genai import ChatGoogleGenerativeAI

from config import (
    GOOGLE_API_KEY, LLM_MAX_OUTPUT_TOKENS, LLM_MAX_RETRIES, LLM_MODEL, LLM_MODEL_FAST,
    LLM_TEMPERATURE, LLM_THINKING_LEVEL, LLM_TIMEOUT_LONG, LLM_TIMEOUT_SHORT,
)


def _thinking_kwargs(model: str, level: str) -> dict:
    """Map a thinking level onto the parameter this model family understands."""
    if model.startswith("gemini-2."):
        return {"thinking_budget": 0 if level in ("off", "minimal") else 1024}
    if level == "off":
        level = "minimal"
    return {"thinking_level": level}


def answer_llm() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=LLM_MODEL,
        google_api_key=GOOGLE_API_KEY,
        temperature=LLM_TEMPERATURE,
        max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        timeout=LLM_TIMEOUT_LONG,
        max_retries=LLM_MAX_RETRIES,
        **_thinking_kwargs(LLM_MODEL, LLM_THINKING_LEVEL),
    )


def fast_llm() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=LLM_MODEL_FAST,
        google_api_key=GOOGLE_API_KEY,
        temperature=0,
        timeout=LLM_TIMEOUT_SHORT,
        max_retries=LLM_MAX_RETRIES,
        **_thinking_kwargs(LLM_MODEL_FAST, "off"),
    )
