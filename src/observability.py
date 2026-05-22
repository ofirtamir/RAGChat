"""
Langfuse observability integration for tracing the RAG pipeline.
Uses the LangChain CallbackHandler for session/user tracking.
Gracefully degrades if langfuse is not installed or credentials are missing.
"""

import os
import uuid
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
        logger.info(
            "Langfuse disabled — LANGFUSE_SECRET_KEY / LANGFUSE_PUBLIC_KEY not set. "
            "Set them as environment variables (Railway dashboard in production)."
        )
        return False

    try:
        # Ensure env vars are set so the SDK picks them up automatically
        os.environ["LANGFUSE_SECRET_KEY"] = LANGFUSE_SECRET_KEY
        os.environ["LANGFUSE_PUBLIC_KEY"] = LANGFUSE_PUBLIC_KEY
        os.environ["LANGFUSE_HOST"] = LANGFUSE_HOST

        from langfuse import Langfuse
        client = Langfuse()
        client.auth_check()
        _langfuse_initialized = True
        logger.info("✅ Langfuse initialized and authenticated (host=%s)", LANGFUSE_HOST)
        return True
    except ImportError:
        logger.warning("langfuse package not installed — pip install langfuse")
        return False
    except Exception as e:
        logger.warning("⚠️  Langfuse auth_check failed: %s", e)
        return False


def is_langfuse_enabled() -> bool:
    """Return True only when credentials are present and auth succeeded."""
    return LANGFUSE_ENABLED and _langfuse_initialized


def flush_langfuse() -> None:
    """Flush pending Langfuse events via the singleton client."""
    if not is_langfuse_enabled():
        return
    try:
        from langfuse import Langfuse
        Langfuse().flush()
    except Exception:
        pass


from contextlib import contextmanager


@contextmanager
def start_trace_span(
    name: str,
    user_id: str | None = None,
    session_id: str | None = None,
    input_data: dict | None = None,
):
    """
    Open an explicit Langfuse parent span and attach user/session metadata to
    its trace.  All LangChain callback spans nested inside this context become
    children of the span and inherit its trace — so the trace shows up in
    Langfuse with user_id / session_id properly attributed.

    Works around the v3 LangChain integration bug where langfuse_user_id /
    langfuse_session_id in run metadata never reach the parent trace.
    See https://github.com/orgs/langfuse/discussions/8493
    """
    if not is_langfuse_enabled():
        yield None
        return

    span = None
    try:
        from langfuse import Langfuse
        client = Langfuse()
        span = client.start_as_current_span(name=name, input=input_data or {}).__enter__()
        try:
            span.update_trace(
                user_id=user_id,
                session_id=session_id,
                name=name,
            )
        except Exception as e:
            logger.warning("Failed to update_trace: %s", e)
    except Exception as e:
        logger.warning("Failed to start Langfuse trace span: %s", e)

    try:
        yield span
    finally:
        if span is not None:
            try:
                span.__exit__(None, None, None)
            except Exception:
                pass


def get_langfuse_handler(
    session_id: str | None = None,
    user_id: str | None = None,
    trace_name: str = "rag-pipeline",
    metadata: dict | None = None,
):
    """
    Create a Langfuse CallbackHandler for LangChain/LangGraph tracing.

    Pass the returned handler in the LangChain/LangGraph config dict:
        config = {"callbacks": [handler]}

    Returns (handler, {}) or (None, {}) when unavailable.
    """
    if not LANGFUSE_ENABLED or not _langfuse_initialized:
        return None, {}

    try:
        from langfuse.langchain import CallbackHandler

        # In langfuse v3, the LangchainCallbackHandler takes NO constructor
        # args EXCEPT update_trace.  Without `update_trace=True`, the
        # langfuse_user_id / langfuse_session_id metadata keys are only
        # applied to child spans — they never propagate to the parent
        # trace, so the trace appears in Langfuse with no user attribution.
        # See https://github.com/orgs/langfuse/discussions/8493
        try:
            handler = CallbackHandler(update_trace=True)
        except TypeError:
            # Fallback for older versions that don't accept update_trace
            handler = CallbackHandler()

        langfuse_metadata = {
            **(metadata or {}),
            "langfuse_session_id": session_id,
            "langfuse_user_id": user_id,
            "langfuse_trace_name": trace_name,
        }

        return handler, langfuse_metadata
    except Exception as e:
        logger.warning("Failed to create Langfuse CallbackHandler: %s", e)
        return None, {}
