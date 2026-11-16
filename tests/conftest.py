"""Tests never call Gemini; a placeholder key lets the clients be constructed without a .env."""
import os

os.environ.setdefault("GOOGLE_API_KEY", "test-key-not-used")
