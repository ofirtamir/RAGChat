"""
Reranker: re-scores retrieved chunks by relevance using a single LLM call.

Flow: retriever returns N candidates → reranker scores them → top K survive.
This filters out noisy / marginally-relevant chunks so the Generator gets
cleaner context and produces deeper, more accurate answers.
"""

import json
import logging

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.documents import Document

from config import GOOGLE_API_KEY, LLM_MODEL, LLM_TIMEOUT_SHORT, LLM_MAX_RETRIES
from src.prompt_store import load_prompt

logger = logging.getLogger(__name__)

RERANK_PROMPT = """You are a relevance judge. Given a user query and a list of text chunks,
score each chunk's relevance to the query on a scale of 0-10.

- 10 = directly answers the query with specific facts/data
- 7-9 = highly relevant, contains important related information
- 4-6 = somewhat relevant, tangentially related
- 1-3 = barely relevant
- 0 = completely irrelevant

User query: {query}

Chunks to score:
{chunks}

Return ONLY a JSON array of scores in the same order as the chunks.
Example for 3 chunks: [8, 3, 6]"""


def _get_reranker_llm() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=LLM_MODEL,
        google_api_key=GOOGLE_API_KEY,
        temperature=0,
        timeout=LLM_TIMEOUT_SHORT,
        max_retries=LLM_MAX_RETRIES,
    )


def rerank_documents(
    query: str,
    documents: list[Document],
    top_k: int = 10,
) -> list[Document]:
    """
    Re-rank documents by relevance to the query using a single LLM call.

    Args:
        query: The search query
        documents: Candidate documents to re-rank
        top_k: Number of top documents to return

    Returns:
        Top-K documents sorted by relevance (highest first)
    """
    if len(documents) <= top_k:
        return documents

    # Build numbered chunk list for the prompt
    chunk_texts = []
    for i, doc in enumerate(documents):
        snippet = doc.page_content[:300].strip()
        chunk_texts.append(f"[{i}] {snippet}")
    chunks_str = "\n\n".join(chunk_texts)

    try:
        llm = _get_reranker_llm()
        # Pull the active prompt from Langfuse (label="production"), falling
        # back to the hard-coded literal above if Langfuse is unavailable.
        # The returned string uses single-brace {var} placeholders, which is
        # exactly what str.format() expects — so no extra conversion needed.
        prompt_template = load_prompt("reranker-system", fallback=RERANK_PROMPT)
        prompt = prompt_template.format(query=query, chunks=chunks_str)
        response = llm.invoke(prompt)
        content = response.content.strip()

        # Parse scores — handle markdown fences if present
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        scores = json.loads(content)

        if not isinstance(scores, list) or len(scores) != len(documents):
            logger.warning("Reranker returned %d scores for %d docs, falling back", len(scores) if isinstance(scores, list) else 0, len(documents))
            return documents[:top_k]

        # Pair documents with scores, sort descending, take top_k
        scored = sorted(zip(documents, scores), key=lambda x: x[1], reverse=True)
        return [doc for doc, _ in scored[:top_k]]

    except Exception as e:
        logger.warning("Reranker failed (%s), returning original order", e)
        return documents[:top_k]
