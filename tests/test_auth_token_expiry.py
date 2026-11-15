"""Unit tests for Google ID token expiry handling in backend/auth.py.

These pin down the behaviour behind the "every request returns 401 about an
hour after sign-in" bug: Google ID tokens live for one hour, and the backend
(correctly) rejects them once they expire. The fix lives in the frontend,
which must refresh the ID token; the backend keeps rejecting stale ones.

No network and no real Google credentials are involved: tokens are signed with
a throwaway RSA key generated for the test run, and the fetch of Google's
signing certificates is patched to return the matching public key.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.security import HTTPAuthorizationCredentials  # noqa: E402
from google.auth import crypt, jwt  # noqa: E402
from google.oauth2 import id_token as google_id_token  # noqa: E402

import backend.auth as auth  # noqa: E402

KEY_ID = "test-key"
CLIENT_ID = "test-client-id.apps.googleusercontent.com"
SUBJECT = "1234567890"
ONE_HOUR = 3600  # lifetime of a Google ID token, in seconds


@pytest.fixture(scope="module")
def signing_key():
    """A throwaway RSA key pair standing in for Google's signing key."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return crypt.RSASigner.from_string(private_pem, KEY_ID), public_pem.decode()


@pytest.fixture
def mint(signing_key, monkeypatch):
    """Return a function that mints a Google-shaped ID token issued `age` seconds ago."""
    signer, public_pem = signing_key
    monkeypatch.setattr(google_id_token, "_fetch_certs", lambda request, url: {KEY_ID: public_pem})
    monkeypatch.setattr(auth, "_GOOGLE_OAUTH_CLIENT_ID", CLIENT_ID)

    def _mint(age: int = 0) -> str:
        issued_at = int(time.time()) - age
        payload = {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "sub": SUBJECT,
            "iat": issued_at,
            "exp": issued_at + ONE_HOUR,
        }
        return jwt.encode(signer, payload).decode()

    return _mint


def test_fresh_token_is_accepted(mint):
    payload = auth.verify_google_id_token(mint(age=60))
    assert payload["sub"] == SUBJECT


def test_token_is_still_accepted_just_before_it_expires(mint):
    payload = auth.verify_google_id_token(mint(age=ONE_HOUR - 60))
    assert payload["sub"] == SUBJECT


def test_token_older_than_one_hour_is_rejected_with_401(mint):
    # Same key, audience, issuer and subject as the accepted tokens above —
    # the only difference is that the sign-in happened 61 minutes ago.
    with pytest.raises(HTTPException) as exc:
        auth.verify_google_id_token(mint(age=ONE_HOUR + 60))

    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid or expired token"
    assert "expired" in str(exc.value.__cause__).lower()


def test_request_dependency_returns_401_for_an_hour_old_session(mint):
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=mint(age=ONE_HOUR + 60))

    with pytest.raises(HTTPException) as exc:
        auth.get_current_user_id(creds)

    assert exc.value.status_code == 401


def test_request_dependency_returns_sub_for_a_fresh_token(mint):
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=mint(age=60))
    assert auth.get_current_user_id(creds) == SUBJECT
