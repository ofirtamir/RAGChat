"""
Query Rewriter node: transforms user questions into optimized search queries.
Strips conversational fluff, extracts key terms, and reformulates for vector search.
"""

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field

from config import GOOGLE_API_KEY, LLM_MODEL, LLM_TIMEOUT_SHORT, LLM_MAX_RETRIES


class RewrittenQuery(BaseModel):
    """Structured output from the query rewriter."""
    original_query: str = Field(description="The original user query")
    rewritten_queries: list[str] = Field(
        description="List of optimized search queries for vector retrieval"
    )
    reasoning: str = Field(description="Brief explanation of the rewriting")


REWRITER_SYSTEM_PROMPT = """You are a query rewriter for a RAG (Retrieval-Augmented Generation) system.
Your job is to transform the user's natural language question into optimized search queries
that will work well with vector similarity search over document chunks.

Rules:
1. Remove conversational filler ("can you tell me", "I want to know", "what does the document say about")
2. Extract the core topic/keywords that would match document content
3. If the query references chat history, resolve the references into a standalone query
4. Generate 1-3 focused search queries (usually 1 is enough, use more for broad questions)
5. Keep queries concise - focus on key terms and concepts
6. Preserve important qualifiers (dates, names, specific terms)
7. Write queries in the same language as the original question

Examples:
- "מה כתוב במסמך על מלחמות ישראל?" → ["מלחמות ישראל"]
- "Can you tell me what the document says about machine learning algorithms?" → ["machine learning algorithms"]
- "What's the difference between TCP and UDP in the networking chapter?" → ["TCP protocol", "UDP protocol"]
- "ספר לי עוד על זה" (after discussing tanks) → ["טנקים"]
- "How many employees does the company have and what's the revenue?" → ["number of employees", "company revenue"]

Return valid JSON:
{{"original_query": "...", "rewritten_queries": ["query1", ...], "reasoning": "brief explanation"}}

Return ONLY the JSON object, no markdown fences or extra text."""

REWRITER_USER_PROMPT = """Chat history (for reference resolution):
{chat_history}

User query: {query}"""


def _get_rewriter_llm() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=LLM_MODEL,
        google_api_key=GOOGLE_API_KEY,
        temperature=0,
        timeout=LLM_TIMEOUT_SHORT,
        max_retries=LLM_MAX_RETRIES,
    )


def rewrite_query(query: str, chat_history: list[dict] | None = None) -> RewrittenQuery:
    """Rewrite a user query into optimized search queries."""
    llm = _get_rewriter_llm()

    # Format chat history
    history_str = "No previous conversation."
    if chat_history:
        history_lines = []
        for msg in chat_history[-6:]:
            role = "User" if msg["role"] == "user" else "Assistant"
            history_lines.append(f"{role}: {msg['content'][:200]}")
        history_str = "\n".join(history_lines)

    prompt = ChatPromptTemplate.from_messages([
        ("system", REWRITER_SYSTEM_PROMPT),
        ("human", REWRITER_USER_PROMPT),
    ])

    parser = JsonOutputParser(pydantic_object=RewrittenQuery)
    chain = prompt | llm | parser

    result = chain.invoke({
        "query": query,
        "chat_history": history_str,
    })

    return RewrittenQuery(**result)


def create_fallback_rewrite(query: str) -> RewrittenQuery:
    """Fallback: use the original query as-is."""
    return RewrittenQuery(
        original_query=query,
        rewritten_queries=[query],
        reasoning="Fallback: using original query.",
    )
