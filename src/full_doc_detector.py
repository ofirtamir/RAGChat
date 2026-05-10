"""
Full Document Detector: decides whether a query requires the entire content
of a specific document rather than just the top-k similar chunks.

Triggered by questions like:
- "סכם את המסמך" / "summarize the document"
- "מה כל הנושאים ב-X?" / "what are all the topics in X?"
- "תאר את כל מה שכתוב ב-X" / "describe everything in X"
"""

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.messages import HumanMessage, AIMessage
from pydantic import BaseModel, Field

from config import GOOGLE_API_KEY, LLM_MODEL


class FullDocDecision(BaseModel):
    """Structured output from the full document detector."""
    needs_full_document: bool = Field(
        description="True if the query requires the entire content of a specific document"
    )
    target_sources: list[str] = Field(
        description="List of source filenames to fetch in full. Empty if needs_full_document=False."
    )
    reasoning: str = Field(description="Brief explanation of the decision")


FULL_DOC_SYSTEM_PROMPT = """You are a retrieval strategy planner for a RAG system.

Your job: decide whether the user's query requires fetching the ENTIRE content of one or more
specific documents, rather than just the most relevant chunks.

Available documents in the knowledge base:
{available_sources}

Classify as NEEDS FULL DOCUMENT (needs_full_document: true) when:
- The user asks to summarize a whole document ("סכם את", "summarize", "תסכם")
- The user asks for ALL topics, themes, or content in a document
- The user asks to list everything in a document ("מה כל...", "what are all...")
- The user wants a comprehensive overview of a document
- The user explicitly names a document and wants full coverage of it

Classify as PARTIAL RETRIEVAL (needs_full_document: false) when:
- The user asks a specific factual question (even if it mentions a document)
- The user wants to find a specific piece of information
- The query is a targeted search (who, what, when, where, how much)
- The query doesn't require holistic understanding of a document

If needs_full_document is true, identify WHICH documents from the available list above.
Match document names flexibly (e.g. "מסמך הטנקים" might match "tanks_report.pdf").
If you cannot identify a specific document, return the most likely match or all documents.

Return valid JSON:
{{"needs_full_document": boolean, "target_sources": ["source1.pdf", ...], "reasoning": "brief explanation"}}

Return ONLY the JSON object."""


def _get_detector_llm() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=LLM_MODEL,
        google_api_key=GOOGLE_API_KEY,
        temperature=0,
    )


def detect_full_document_need(
    query: str,
    available_sources: list[str],
    chat_history: list[dict] | None = None,
) -> FullDocDecision:
    """
    Decide if the query needs full document retrieval.

    Args:
        query: The user query (or rewritten query)
        available_sources: List of source names in the vector store
        chat_history: Recent conversation for context

    Returns:
        FullDocDecision with needs_full_document and target_sources
    """
    if not available_sources:
        return FullDocDecision(
            needs_full_document=False,
            target_sources=[],
            reasoning="No documents available in the knowledge base.",
        )

    llm = _get_detector_llm()

    sources_str = "\n".join(f"- {s}" for s in available_sources)

    # Build history as Message objects to avoid template variable issues
    history_messages = []
    if chat_history:
        for msg in chat_history[-4:]:
            content = msg["content"][:300]
            if msg["role"] == "user":
                history_messages.append(HumanMessage(content=content))
            else:
                history_messages.append(AIMessage(content=content))

    prompt = ChatPromptTemplate.from_messages([
        ("system", FULL_DOC_SYSTEM_PROMPT),
        MessagesPlaceholder("chat_history"),
        ("human", "{query}"),
    ])
    parser = JsonOutputParser(pydantic_object=FullDocDecision)
    chain = prompt | llm | parser

    result = chain.invoke({
        "query": query,
        "available_sources": sources_str,
        "chat_history": history_messages,
    })

    decision = FullDocDecision(**result)

    # Validate: only keep target_sources that actually exist
    valid_sources = [s for s in decision.target_sources if s in available_sources]

    # If no valid matches but LLM said full doc needed, fallback to first source
    if decision.needs_full_document and not valid_sources and available_sources:
        valid_sources = available_sources[:1]

    return FullDocDecision(
        needs_full_document=decision.needs_full_document,
        target_sources=valid_sources,
        reasoning=decision.reasoning,
    )


def create_fallback_full_doc_decision() -> FullDocDecision:
    """Fallback: no full document retrieval."""
    return FullDocDecision(
        needs_full_document=False,
        target_sources=[],
        reasoning="Fallback: using partial retrieval.",
    )
