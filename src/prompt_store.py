"""
Langfuse-backed prompt store.

Centralizes how the application loads its system prompts.  Each prompt is
identified by a stable ``name`` (e.g. ``analyzer-system``) and ships with a
``fallback`` — the literal hard-coded prompt baked into the source code.

When Langfuse is reachable, ``load_prompt`` fetches the version labelled
``production`` (configurable) and caches it via the Langfuse SDK's built-in
TTL cache (default 60s) so subsequent calls are essentially free.

When Langfuse is disabled or unreachable, ``load_prompt`` transparently
returns the fallback string.  This keeps the app fully functional with no
external dependency at runtime — Langfuse becomes a soft enhancement that
lets us iterate on prompts without redeploying.

Usage
─────

    from src.prompt_store import load_prompt

    HARDCODED = "You are a query analyzer ..."

    prompt_text = load_prompt("analyzer-system", fallback=HARDCODED)

    prompt = ChatPromptTemplate.from_messages([
        ("system", prompt_text),
        ("human", "{query}"),
    ])

The returned string is already in LangChain template syntax — variables
inside double Mustache braces (``{{var}}``) in Langfuse are converted to
single braces (``{var}``) on retrieval via ``get_langchain_prompt()``.
"""

from __future__ import annotations

import logging
from typing import Final

from src.observability import is_langfuse_enabled

logger = logging.getLogger(__name__)

# How long to cache a prompt locally before re-fetching from Langfuse.
# 60s is the SDK default; we make it explicit so behaviour is obvious.
_CACHE_TTL_SECONDS: Final[int] = 60

# Which label to fetch by default. Production traffic should always read
# the prompt currently labelled "production" in Langfuse.
_DEFAULT_LABEL: Final[str] = "production"


def load_prompt(
    name: str,
    *,
    fallback: str,
    label: str = _DEFAULT_LABEL,
) -> str:
    """
    Return the prompt text for ``name`` from Langfuse, falling back to the
    hard-coded ``fallback`` string when Langfuse is unavailable.

    The returned string is in LangChain template syntax (single-brace
    variables) so it can be dropped straight into ``ChatPromptTemplate``.

    Args:
        name: Stable Langfuse prompt identifier, e.g. ``analyzer-system``.
        fallback: Literal prompt string baked into the source code.  Used
            verbatim when Langfuse is disabled, unreachable, or doesn't
            yet have a prompt with that name.
        label: Langfuse label to look up.  Defaults to ``production``.

    Returns:
        The prompt text, ready to use with LangChain templates.
    """
    if not is_langfuse_enabled():
        return fallback

    try:
        from langfuse import get_client

        client = get_client()
        prompt = client.get_prompt(
            name,
            label=label,
            cache_ttl_seconds=_CACHE_TTL_SECONDS,
            fallback=fallback,  # SDK-level fallback if the lookup itself fails
        )
        # get_langchain_prompt() converts Mustache ({{var}}) to LangChain
        # ({var}) and escapes literal braces appropriately.
        return prompt.get_langchain_prompt()
    except Exception as e:
        logger.warning(
            "Langfuse get_prompt(%r) failed (%s); using hard-coded fallback.",
            name,
            e,
        )
        return fallback
