"""
Query-rewriter types and fallback helper.

The original ``rewrite_query`` LLM call and ``REWRITER_*`` prompts were
folded into ``query_analyzer`` (one LLM call now performs routing AND
rewriting).  This module is kept only for ``RewrittenQuery`` (the typed
return value still used downstream) and ``create_fallback_rewrite``
(used when the analyzer fails or the path skips analysis).
"""

from pydantic import BaseModel, Field


class RewrittenQuery(BaseModel):
    """Structured output from the query rewriter."""

    original_query: str = Field(description="The original user query")
    rewritten_queries: list[str] = Field(
        description="List of optimized search queries for vector retrieval"
    )
    reasoning: str = Field(description="Brief explanation of the rewriting")


def create_fallback_rewrite(query: str) -> RewrittenQuery:
    """Fallback: use the original query as-is."""
    return RewrittenQuery(
        original_query=query,
        rewritten_queries=[query],
        reasoning="Fallback: using original query.",
    )
