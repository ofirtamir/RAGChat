import threading

from langchain_chroma import Chroma
from langchain_core.documents import Document

from src.embeddings import get_embedding_model
from config import CHROMA_DB_DIR, CHROMA_COLLECTION_NAME

# ── Singleton vector store ───────────────────────────────────────────────
# Reuse a single Chroma instance across all calls to avoid re-opening the
# underlying persistent connection on every search / count / list operation.
_vector_store: Chroma | None = None
_vs_lock = threading.Lock()


def get_vector_store() -> Chroma:
    """Return the shared persistent ChromaDB vector store (singleton)."""
    global _vector_store
    if _vector_store is not None:
        return _vector_store
    with _vs_lock:
        # Double-check after acquiring the lock.
        if _vector_store is None:
            _vector_store = Chroma(
                collection_name=CHROMA_COLLECTION_NAME,
                embedding_function=get_embedding_model(),
                persist_directory=str(CHROMA_DB_DIR),
            )
        return _vector_store


def add_documents(documents: list[Document]) -> None:
    """Add documents to the vector store."""
    vs = get_vector_store()
    vs.add_documents(documents)


def clear_vector_store() -> None:
    """Delete all documents from the collection and invalidate the singleton."""
    global _vector_store
    vs = get_vector_store()
    vs.delete_collection()
    with _vs_lock:
        _vector_store = None


def get_document_count() -> int:
    """Return the number of chunks stored in the vector store."""
    vs = get_vector_store()
    return vs._collection.count()


def list_document_sources() -> list[str]:
    """Return a sorted list of unique document source names in the store.

    Uses a paginated scan with a small page size so that only source fields
    are touched — avoids pulling every chunk's full metadata dict into RAM.
    """
    vs = get_vector_store()
    collection = vs._collection
    total = collection.count()
    if total == 0:
        return []

    # Paginate through the collection extracting only the 'source' field.
    sources: set[str] = set()
    page_size = 5000
    offset = 0
    while offset < total:
        batch = collection.get(
            include=["metadatas"],
            limit=page_size,
            offset=offset,
        )
        for meta in batch["metadatas"]:
            if meta:
                sources.add(meta.get("source", "unknown"))
        offset += page_size

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
