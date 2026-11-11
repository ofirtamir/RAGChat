from pathlib import Path

from langchain_community.document_loaders import (
    TextLoader,
    UnstructuredMarkdownLoader,
)
from langchain_core.documents import Document

from src.pdf_parser import parse_pdf

LOADER_MAP = {
    ".txt": TextLoader,
    ".md": UnstructuredMarkdownLoader,
}

SUPPORTED_EXTENSIONS = [".pdf", *LOADER_MAP.keys()]


def _load_pdf(path: Path) -> list[Document]:
    """One Document per non-empty page; `page` is 0-based like PyPDFLoader's."""
    return [
        Document(page_content=page.markdown, metadata={"page": page.number})
        for page in parse_pdf(path)
        if page.markdown
    ]


def load_document(file_path: str | Path) -> list[Document]:
    """Load a single document and return a list of LangChain Documents."""
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        docs = _load_pdf(path)
    else:
        loader_cls = LOADER_MAP.get(suffix)
        if loader_cls is None:
            raise ValueError(
                f"Unsupported file type: {suffix}. "
                f"Supported types: {SUPPORTED_EXTENSIONS}"
            )
        # TextLoader may need explicit encoding on Windows
        if loader_cls is TextLoader:
            loader = loader_cls(str(path), encoding="utf-8")
        else:
            loader = loader_cls(str(path))
        docs = loader.load()

    for doc in docs:
        doc.metadata["source"] = path.name

    return docs


def load_documents(file_paths: list[str | Path]) -> list[Document]:
    """Load multiple documents and return a flat list of Documents."""
    all_docs = []
    for fp in file_paths:
        all_docs.extend(load_document(fp))
    return all_docs
