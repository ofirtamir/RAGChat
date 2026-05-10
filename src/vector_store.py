from langchain_chroma import Chroma
from langchain_core.documents import Document

from src.embeddings import get_embedding_model
from config import CHROMA_DB_DIR, CHROMA_COLLECTION_NAME


def get_vector_store() -> Chroma:
    """Get or create the persistent ChromaDB vector store."""
    return Chroma(
        collection_name=CHROMA_COLLECTION_NAME,
        embedding_function=get_embedding_model(),
        persist_directory=str(CHROMA_DB_DIR),
    )


def add_documents(documents: list[Document]) -> None:
    """Add documents to the vector store."""
    vs = get_vector_store()
    vs.add_documents(documents)


def clear_vector_store() -> None:
    """Delete all documents from the collection."""
    vs = get_vector_store()
    vs.delete_collection()


def get_document_count() -> int:
    """Return the number of chunks stored in the vector store."""
    vs = get_vector_store()
    return vs._collection.count()


def list_document_sources() -> list[str]:
    """Return a sorted list of unique document source names in the store."""
    vs = get_vector_store()
    result = vs._collection.get(include=["metadatas"])
    sources = {
        meta.get("source", "unknown")
        for meta in result["metadatas"]
        if meta
    }
    return sorted(sources)


def get_all_chunks_for_source(source: str) -> list[Document]:
    """Return ALL chunks belonging to a specific source document, sorted by page."""
    vs = get_vector_store()
    result = vs._collection.get(
        where={"source": source},
        include=["documents", "metadatas"],
    )

    docs = []
    for text, meta in zip(result["documents"], result["metadatas"]):
        docs.append(Document(page_content=text, metadata=meta or {}))

    # Sort by page number if available
    docs.sort(key=lambda d: d.metadata.get("page", 0))
    return docs
