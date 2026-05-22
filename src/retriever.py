from langchain_core.documents import Document

from src.vector_store import get_vector_store
from src.reranker import rerank_documents
from config import RETRIEVER_K, RETRIEVER_CANDIDATES


def get_retriever(k: int = RETRIEVER_CANDIDATES):
    """Return a retriever that fetches candidates for reranking."""
    vs = get_vector_store()
    return vs.as_retriever(
        search_type="mmr",
        search_kwargs={"k": k, "fetch_k": k * 3},
    )


def retrieve_for_query(query: str) -> list[Document]:
    """Retrieve and rerank relevant documents for a single query string.

    1. Fetch RETRIEVER_CANDIDATES (20) via MMR for diversity
    2. Rerank by relevance with LLM scoring
    3. Return top RETRIEVER_K (10)
    """
    retriever = get_retriever()
    candidates = retriever.invoke(query)
    return rerank_documents(query, candidates, top_k=RETRIEVER_K)
