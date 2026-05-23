"""
Planner types and fallback helper.

The original ``create_query_plan`` LLM call and ``PLANNER_*`` prompts were
superseded by ``query_analyzer``: the analyzer's rewritten queries are
used directly to build a deterministic plan inside ``retriever_node``
(see ``graph.py``).  This module is kept only for ``QueryPlan`` (the
typed value still used as graph state) and ``create_fallback_plan``.
"""

from pydantic import BaseModel, Field


class QueryPlan(BaseModel):
    """Structured output from the query planner."""

    is_complex: bool = Field(
        description="Whether the query needs decomposition into sub-queries"
    )
    sub_queries: list[str] = Field(
        description="List of sub-queries. For simple queries, contains just the original query."
    )
    reasoning: str = Field(
        description="Brief explanation of the planning decision"
    )


def create_fallback_plan(query: str) -> QueryPlan:
    """Create a simple fallback plan when no analyzer output is available."""
    return QueryPlan(
        is_complex=False,
        sub_queries=[query],
        reasoning="Fallback: treating as simple query.",
    )
