from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field

from config import GOOGLE_API_KEY, LLM_MODEL, PLANNER_MAX_SUBQUERIES, LLM_TIMEOUT_SHORT, LLM_MAX_RETRIES


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


PLANNER_SYSTEM_PROMPT = """You are a query planner for a RAG (Retrieval-Augmented Generation) system.
Analyze the user's query and decide:
1. Is this a simple, focused query that can be answered with a single document retrieval?
2. Or is it complex, requiring information from multiple topics or comparisons?

For simple queries, return the original query as the only sub-query.
For complex queries, decompose into {max_subqueries} or fewer focused sub-queries.

Examples of complex queries needing decomposition:
- "Compare X and Y" -> separate queries for X and Y
- "What is X and how does it relate to Y?" -> query for X, query for Y, query for X-Y relationship
- "Summarize the main themes across all documents" -> queries for each potential theme

Return valid JSON matching this schema:
{{
    "is_complex": boolean,
    "sub_queries": ["query1", "query2", ...],
    "reasoning": "brief explanation"
}}

Return ONLY the JSON object, no markdown fences or extra text."""

PLANNER_USER_PROMPT = "Query: {query}"


def _get_planner_llm() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=LLM_MODEL,
        google_api_key=GOOGLE_API_KEY,
        temperature=0,
        timeout=LLM_TIMEOUT_SHORT,
        max_retries=LLM_MAX_RETRIES,
    )


def create_query_plan(query: str) -> QueryPlan:
    """Analyze a query and produce a plan (single or multi-sub-query)."""
    llm = _get_planner_llm()

    prompt = ChatPromptTemplate.from_messages([
        ("system", PLANNER_SYSTEM_PROMPT),
        ("human", PLANNER_USER_PROMPT),
    ])

    parser = JsonOutputParser(pydantic_object=QueryPlan)

    chain = prompt | llm | parser

    result = chain.invoke({
        "query": query,
        "max_subqueries": PLANNER_MAX_SUBQUERIES,
    })

    return QueryPlan(**result)


def create_fallback_plan(query: str) -> QueryPlan:
    """Create a simple fallback plan when the planner fails."""
    return QueryPlan(
        is_complex=False,
        sub_queries=[query],
        reasoning="Fallback: treating as simple query.",
    )
