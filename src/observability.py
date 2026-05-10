"""
Langfuse v4 observability integration for tracing the RAG pipeline.
Uses the LangChain CallbackHandler with metadata-based session/user tracking.
Gracefully degrades if langfuse is not installed.
"""

import os
import logging

from config import (
    LANGFUSE_SECRET_KEY,
    LANGFUSE_PUBLIC_KEY,
    LANGFUSE_HOST,
    LANGFUSE_ENABLED,
)

logger = logging.getLogger(__name__)

_langfuse_initialized = False


def initialize_langfuse() -> bool:
    """
    Set environment variables and verify Langfuse connectivity.
    Must be called once on app startup, before any handler is created.
    """
    global _langfuse_initialized

    if not LANGFUSE_ENABLED:
        return False

    try:
        # Langfuse v4 reads credentials from env vars
        os.environ["LANGFUSE_SECRET_KEY"] = LANGFUSE_SECRET_KEY
        os.environ["LANGFUSE_PUBLIC_KEY"] = LANGFUSE_PUBLIC_KEY
        os.environ["LANGFUSE_HOST"] = LANGFUSE_HOST

        from langfuse import Langfuse
        client = Langfuse()
        client.auth_check()
        _langfuse_initialized = True
        logger.info("Langfuse initialized and authenticated successfully")
        return True
    except ImportError:
        logger.warning("langfuse package not installed. Install with: pip install langfuse")
        return False
    except Exception as e:
        logger.warning(f"Failed to initialize Langfuse: {e}")
        return False


def get_langfuse_handler(
    session_id: str | None = None,
    user_id: str | None = None,
    trace_name: str = "rag-pipeline",
    metadata: dict | None = None,
):
    """
    Create a Langfuse CallbackHandler for LangChain/LangGraph tracing.

    In Langfuse v4, session_id and user_id are passed via LangChain run
    metadata keys: 'langfuse_session_id' and 'langfuse_user_id'.

    Returns a (handler, config_metadata) tuple, or (None, {}) if unavailable.
    """
    if not LANGFUSE_ENABLED or not _langfuse_initialized:
        return None, {}

    try:
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler

        # Create a unique trace_id
        client = Langfuse()
        trace_id = client.create_trace_id()

        # Create handler linked to this trace
        handler = CallbackHandler(
            trace_context={"trace_id": trace_id},
        )

        # Langfuse v4 reads these special metadata keys from LangChain runs
        langfuse_metadata = {
            **(metadata or {}),
            "langfuse_session_id": session_id,
            "langfuse_user_id": user_id,
        }

        return handler, langfuse_metadata
    except ImportError:
        logger.warning("langfuse.langchain not available")
        return None, {}
    except Exception as e:
        logger.warning(f"Failed to create Langfuse handler: {e}")
        return None, {}
