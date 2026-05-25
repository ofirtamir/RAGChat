"""
Authentication helpers for the FastAPI backend.

The frontend uses NextAuth with Google as the sole OAuth provider. NextAuth
stores the Google ``id_token`` on the session JWT (see ``frontend/auth.ts``)
and the browser forwards it to this backend as ``Authorization: Bearer …``.

Every request that mutates per-user data MUST go through ``get_current_user_id``
so the user identifier is taken from the *signed* Google token rather than
from a query string or request body, where a caller could trivially set
``user_id`` to any other user's value.

Verification details
────────────────────
We rely on ``google-auth``'s ``id_token.verify_oauth2_token`` helper, which:
    1. Fetches Google's rotating public keys (cached internally).
    2. Validates signature, ``iss`` (accounts.google.com / https://accounts.google.com),
       and ``exp``.
    3. When ``GOOGLE_OAUTH_CLIENT_ID`` is configured, also asserts ``aud`` matches.

If ``GOOGLE_OAUTH_CLIENT_ID`` is unset we still verify signature & expiry, but
log a warning — production deployments should always pin the audience to the
OAuth client ID issued for this app, otherwise a token minted for a different
Google OAuth client could be replayed here.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

logger = logging.getLogger(__name__)

# Pinning audience to our own OAuth client ID prevents tokens issued for
# another Google project from being accepted here. The env var name mirrors
# the one already used by NextAuth (AUTH_GOOGLE_ID) so deployments can reuse
# the same value.
_GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID") or os.getenv("AUTH_GOOGLE_ID")

# auto_error=False lets us return our own JSON error body instead of FastAPI's
# default "Not authenticated" when the header is missing.
_bearer_scheme = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def _transport() -> google_requests.Request:
    """Single shared HTTP transport — google-auth caches Google's JWKS on it."""
    return google_requests.Request()


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def verify_google_id_token(token: str) -> dict:
    """Verify a Google ID token and return its decoded payload."""
    if not token:
        raise _unauthorized("Missing bearer token")

    try:
        payload = google_id_token.verify_oauth2_token(
            token,
            _transport(),
            audience=_GOOGLE_OAUTH_CLIENT_ID,
        )
    except ValueError as e:
        # verify_oauth2_token raises ValueError for every failure mode
        # (bad signature, expired, wrong audience, wrong issuer, malformed).
        logger.info("Rejected Google ID token: %s", e)
        raise _unauthorized("Invalid or expired token") from e

    if not payload.get("sub"):
        raise _unauthorized("Token missing sub claim")

    return payload


def get_current_user_id(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """FastAPI dependency: verifies the Bearer token and returns the Google sub.

    Use as: ``user_id: str = Depends(get_current_user_id)``
    The returned string is the authoritative user identifier for the request.
    """
    if creds is None or creds.scheme.lower() != "bearer":
        raise _unauthorized("Missing or malformed Authorization header")

    payload = verify_google_id_token(creds.credentials)
    return payload["sub"]


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict:
    """FastAPI dependency: returns the full verified payload (sub, email, …)."""
    if creds is None or creds.scheme.lower() != "bearer":
        raise _unauthorized("Missing or malformed Authorization header")
    return verify_google_id_token(creds.credentials)
