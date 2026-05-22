import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Paths
# DATA_DIR and UPLOADS_DIR can be overridden via env vars so that a Docker
# volume mount (e.g. /app/data) is used in production instead of the image FS.
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(PROJECT_ROOT / "data")))
CHROMA_DB_DIR = DATA_DIR / "chroma_db"
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", str(PROJECT_ROOT / "uploads")))

# Ensure directories exist
CHROMA_DB_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# API Keys
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Langfuse Observability
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY")
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
LANGFUSE_ENABLED = bool(LANGFUSE_SECRET_KEY and LANGFUSE_PUBLIC_KEY)

# LLM settings
# Allow overriding via environment variables to match available models per API key/project.
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.5-flash")
LLM_TEMPERATURE = 0.3
LLM_MAX_OUTPUT_TOKENS = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "8192"))

# LLM reliability settings
# Short timeout for decision nodes (Router, Rewriter, Planner, FullDocDetector)
# that return small JSON responses. Long timeout for generation nodes
# (Generator, Chitchat) that may produce thousands of tokens.
LLM_TIMEOUT_SHORT = int(os.getenv("LLM_TIMEOUT_SHORT", "30"))    # seconds
LLM_TIMEOUT_LONG = int(os.getenv("LLM_TIMEOUT_LONG", "120"))     # seconds
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))

# Embedding settings
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "models/gemini-embedding-001")

# Chunking settings
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200

# Retriever settings
RETRIEVER_K = 10

# ChromaDB
CHROMA_COLLECTION_NAME = "ragchat_documents"

# Planner
PLANNER_MAX_SUBQUERIES = 4
