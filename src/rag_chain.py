from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.documents import Document

from src.planner import create_query_plan, create_fallback_plan, QueryPlan
from src.retriever import retrieve_for_query
from src.vector_store import get_document_count
from config import (
    GOOGLE_API_KEY, LLM_MODEL, LLM_TEMPERATURE, LLM_MAX_OUTPUT_TOKENS,
    LLM_TIMEOUT_LONG, LLM_MAX_RETRIES,
)


ANSWER_SYSTEM_PROMPT = """You are a helpful assistant that answers questions based on the provided context.
Use ONLY the context below to answer. If the context doesn't contain enough information, say so clearly.
Do not make up information. Cite the source document when possible.

Context:
{context}"""

SYNTHESIS_SYSTEM_PROMPT = """You are a helpful assistant that synthesizes information from multiple retrievals.
Below are results from multiple sub-queries related to the user's original question.
Synthesize these into a coherent, comprehensive answer.
Use ONLY the provided information. If some sub-queries returned no useful results, note that.

{sub_query_results}"""


def _get_llm() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=LLM_MODEL,
        google_api_key=GOOGLE_API_KEY,
        temperature=LLM_TEMPERATURE,
        max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        timeout=LLM_TIMEOUT_LONG,
        max_retries=LLM_MAX_RETRIES,
    )


def _format_docs(docs: list[Document]) -> str:
    """Format retrieved documents into a readable context string."""
    if not docs:
        return "No relevant documents found."

    formatted = []
    for i, doc in enumerate(docs, 1):
        source = doc.metadata.get("source", "unknown")
        page = doc.metadata.get("page", "")
        page_str = f" (page {page + 1})" if page != "" else ""
        formatted.append(f"[{i}] Source: {source}{page_str}\n{doc.page_content}")
    return "\n\n---\n\n".join(formatted)


def _extract_sources(docs: list[Document]) -> list[str]:
    """Extract unique source names from documents."""
    return list({doc.metadata.get("source", "unknown") for doc in docs})


def _answer_simple_query(query: str, docs: list[Document]) -> str:
    """Generate an answer for a simple (single-retrieval) query."""
    llm = _get_llm()
    context = _format_docs(docs)

    prompt = ChatPromptTemplate.from_messages([
        ("system", ANSWER_SYSTEM_PROMPT),
        ("human", "{query}"),
    ])

    chain = prompt | llm | StrOutputParser()
    return chain.invoke({"context": context, "query": query})


def _answer_complex_query(
    original_query: str,
    plan: QueryPlan,
) -> tuple[str, list[Document]]:
    """Execute sub-queries, retrieve for each, and synthesize a final answer."""
    sub_results = []
    all_docs = []

    for sq in plan.sub_queries:
        docs = retrieve_for_query(sq)
        all_docs.extend(docs)
        sub_results.append(
            f"Sub-query: {sq}\nRetrieved context:\n{_format_docs(docs)}"
        )

    llm = _get_llm()
    combined = "\n\n===\n\n".join(sub_results)

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYNTHESIS_SYSTEM_PROMPT),
        ("human", "{query}"),
    ])

    chain = prompt | llm | StrOutputParser()
    answer = chain.invoke({
        "sub_query_results": combined,
        "query": original_query,
    })

    return answer, all_docs


def run_rag_pipeline(query: str) -> dict:
    """
    Main RAG pipeline entry point.

    Returns a dict with:
        - answer: The generated answer string
        - plan: The QueryPlan used
        - sources: List of source document names
    """
    # Check if there are documents in the store
    if get_document_count() == 0:
        return {
            "answer": "No documents have been uploaded yet. Please upload documents first using the sidebar.",
            "plan": create_fallback_plan(query),
            "sources": [],
        }

    # Step 1: Plan
    try:
        plan = create_query_plan(query)
    except Exception:
        plan = create_fallback_plan(query)

    # Step 2: Retrieve and Generate
    try:
        if not plan.is_complex:
            docs = retrieve_for_query(plan.sub_queries[0])
            answer = _answer_simple_query(query, docs)
            sources = _extract_sources(docs)
        else:
            answer, all_docs = _answer_complex_query(query, plan)
            sources = _extract_sources(all_docs)
    except Exception:
        answer = (
            "⚠️ מצטער, לא הצלחתי לייצר תשובה כרגע. "
            "ייתכן שיש עומס על השרת — נסה שוב בעוד כמה שניות."
        )
        sources = []

    return {
        "answer": answer,
        "plan": plan,
        "sources": sources,
    }
