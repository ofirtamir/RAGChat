"""
Router types.

The original ``route_query`` LLM call and ``ROUTER_SYSTEM_PROMPT`` were
folded into ``query_analyzer`` (one LLM call now performs routing AND
rewriting).  This module is kept only as the home of ``RouteDecision``,
which other modules still use as a typed return value.
"""

from pydantic import BaseModel, Field


class RouteDecision(BaseModel):
    """Structured output from the router."""

    needs_retrieval: bool = Field(
        description="True if the query requires searching documents, False for chitchat"
    )
    reasoning: str = Field(
        description="Brief explanation of the routing decision"
    )
