"""Minimal client for gov.il dynamic-collector open APIs.

Lets the deployed backend pull documents straight from gov.il — no local
files needed. Mirrors the govil-scraper project's behavior:
- search API needs an ``x-client-id`` header; results are newest-first
- ``skip`` paginates in steps of 10 (deep pagination is slow — we only pull
  small recent batches here)
- the document host (free-justice.openapi.gov.il) only accepts legacy TLS
  ciphers, so downloads go through a dedicated SSL context
- record identity = sha1 of the canonical record JSON (same as govil-scraper,
  so locally-ingested and server-ingested chunks share ids and dedupe)
"""
import hashlib
import io
import json
import ssl
import time

import requests
from requests.adapters import HTTPAdapter

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class _LegacyTLSAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def record_key(data: dict) -> str:
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


class GovilClient:
    def __init__(self, search_url: str, x_client_id: str, delay: float = 0.3, timeout: int = 90):
        self.search_url = search_url
        self.delay = delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.mount("https://", _LegacyTLSAdapter())
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "x-client-id": x_client_id,
        })

    def latest_records(self, limit: int) -> list[dict]:
        """Return the newest `limit` record Data dicts (API sorts newest-first)."""
        records: list[dict] = []
        skip = 0
        while len(records) < limit:
            resp = self.session.post(
                self.search_url,
                data=json.dumps({"skip": skip}, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json;charset=utf-8"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            results = resp.json().get("Results") or []
            if not results:
                break
            for item in results:
                data = item.get("Data") or {}
                if data:
                    records.append(data)
            skip += 10
            time.sleep(self.delay)
        return records[:limit]

    @staticmethod
    def document_urls(data: dict) -> list[dict]:
        """Find downloadable documents in a record generically: any list of
        dicts carrying a FileName URL."""
        docs = []
        for value in data.values():
            items = value if isinstance(value, list) else [value]
            for item in items:
                if isinstance(item, dict) and str(item.get("FileName", "")).startswith("http"):
                    docs.append(item)
        return docs

    def download(self, url: str) -> bytes:
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.content


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Local text extraction with pypdf — no external OCR services."""
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join((page.extract_text() or "") for page in reader.pages).strip()
