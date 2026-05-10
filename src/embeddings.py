from langchain_google_genai import GoogleGenerativeAIEmbeddings

from config import GOOGLE_API_KEY, EMBEDDING_MODEL


def get_embedding_model() -> GoogleGenerativeAIEmbeddings:
    """Return the configured Google embedding model."""
    return GoogleGenerativeAIEmbeddings(
        model=EMBEDDING_MODEL,
        google_api_key=GOOGLE_API_KEY,
    )
