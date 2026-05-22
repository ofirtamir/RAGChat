# ── Backend Dockerfile ─────────────────────────────────────────────────────
# Builds the FastAPI + LangGraph + ChromaDB backend.
# The /app/data directory should be mounted as a persistent volume in production
# so that the ChromaDB index and uploaded files survive restarts.

FROM python:3.11-slim

# System dependencies:
#   libmagic1      → python-magic / unstructured file-type detection
#   poppler-utils  → pypdf / PDF text extraction
#   tesseract-ocr  → OCR fallback inside unstructured (optional but avoids import errors)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libmagic1 \
        poppler-utils \
        tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (better Docker layer caching)
COPY requirements.txt ./requirements.txt
COPY backend/requirements-backend.txt ./requirements-backend.txt
RUN pip install --no-cache-dir -r requirements.txt -r requirements-backend.txt

# Copy application source
COPY . .

# Pre-create data & uploads directories (they may be overridden by a volume mount)
RUN mkdir -p data/chroma_db uploads

EXPOSE 8000

# Railway injects $PORT; fall back to 8000 for local docker run
# --timeout-keep-alive 650 keeps SSE connections alive for long LLM responses
# (must exceed the LLM generation timeout so long answers aren't cut off)
CMD ["sh", "-c", "uvicorn backend.api:app --host 0.0.0.0 --port ${PORT:-8000} --timeout-keep-alive 650"]
