from langchain_core.documents import Document

from src.vector_store import get_vector_store
from src.reranker import rerank_documents
from config import RETRIEVER_K, RETRIEVER_CANDIDATES


def _get_store(agent_id: str | None):
    if agent_id:
        from src.agents import get_agent_vector_store
        return get_agent_vector_store(agent_id)
    return get_vector_store()


def get_retriever(k: int = RETRIEVER_CANDIDATES, agent_id: str | None = None,
                  where: dict | None = None):
    """Return a retriever that fetches candidates for reranking.

    agent_id selects an agent's dedicated collection (default: uploads store).
    where is a Chroma metadata filter (see src.agents.build_chroma_where).
    """
    vs = _get_store(agent_id)
    search_kwargs: dict = {"k": k, "fetch_k": k * 3}
    if where:
        search_kwargs["filter"] = where
    return vs.as_retriever(search_type="mmr", search_kwargs=search_kwargs)


def retrieve_for_query(query: str, agent_id: str | None = None,
                       where: dict | None = None) -> list[Document]:
    """Retrieve and rerank relevant documents for a single query string.

    1. Fetch RETRIEVER_CANDIDATES (20) via MMR for diversity
    2. Rerank by relevance with LLM scoring
    3. Return top RETRIEVER_K (10)
    """
    retriever = get_retriever(agent_id=agent_id, where=where)
    candidates = retriever.invoke(query)
    return rerank_documents(query, candidates, top_k=RETRIEVER_K)
