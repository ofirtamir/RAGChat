from langchain_core.documents import Document

from src.vector_store import get_vector_store
from config import RETRIEVER_K


def get_retriever():
    """Return a retriever that performs similarity search."""
    vs = get_vector_store()
    return vs.as_retriever(
        search_type="mmr",
        search_kwargs={"k": RETRIEVER_K, "fetch_k": RETRIEVER_K * 3},
    )


def retrieve_for_query(query: str) -> list[Document]:
    """Retrieve relevant documents for a single query string."""
    retriever = get_retriever()
    return retriever.invoke(query)
