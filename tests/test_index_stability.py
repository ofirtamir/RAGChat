"""Index consistency as documents come and go: citation numbering across sub-queries,
no full index scan per question, maintenance of the index, failed re-indexing,
late worker results, and resuming a large interrupted load."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from langchain_core.documents import Document  # noqa: E402

from backend import admin_api, auth  # noqa: E402
from src import admin_store, graph, hybrid_index, library_batch, library_index, library_store  # noqa: E402

AGENT = {"id": "test_agent", "name": "בדיקה", "filters": [
    {"name": "AppraisalHeader", "label": "כותרת", "type": "text"},
    {"name": "Committee", "label": "ועדה", "type": "choice", "values": ["ירושלים"]},
    {"name": "PublicityDate", "label": "פרסום", "type": "date"}]}


def record(i: int) -> dict:
    return {"AppraisalHeader": f"הכרעה {i}", "Committee": "ירושלים", "PublicityDate": "2025-03-01",
            "Document": [{"FileName": f"https://example.org/doc/{i}", "DisplayName": f"הכרעה {i}",
                          "Extension": "pdf"}], "DocSummary": {}}


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "admin.db"
    monkeypatch.setattr(admin_store, "_DB_PATH", db)
    monkeypatch.setattr(library_store, "_DB_PATH", db)
    admin_store._init_db()
    library_store._init_db()
    monkeypatch.setattr("src.agents.get_agent", lambda agent_id: AGENT if agent_id == AGENT["id"] else None)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(admin_api, "_agent", lambda agent_id: AGENT)
    monkeypatch.setattr(library_index, "enqueue", lambda agent_id, keys: None)
    app = FastAPI()
    app.include_router(admin_api.worker)
    app.dependency_overrides[auth.require_worker] = lambda: None
    return TestClient(app)


def _keys(n: int) -> list[str]:
    library_store.upsert_catalog(AGENT, [record(i) for i in range(n)])
    return [d["record_key"] for d in library_store.search(AGENT, page_size=200)["documents"]]


# ── citations ────────────────────────────────────────────────────────────

def test_citation_numbers_continue_across_sub_queries():
    docs = [Document(page_content=t, metadata={"source": s, "_sub_query": q})
            for t, s, q in [("א", "d1", "q1"), ("ב", "d2", "q2"), ("ג", "d3", "q1"), ("ד", "d4", "q2")]]
    context = graph._sub_query_context(["q1", "q2"], docs)
    q1, q2 = context.split("===")
    assert "[1] Source: d1" in q1 and "[3] Source: d3" in q1
    assert "[2] Source: d2" in q2 and "[4] Source: d4" in q2       # not restarted at [1]
    assert [c["source"] for c in graph._extract_citations(docs)] == ["d1", "d2", "d3", "d4"]


# ── no full scan per question ────────────────────────────────────────────

def test_a_large_library_is_recognised_without_reading_the_index(monkeypatch):
    keys = _keys(3)
    for k in keys:
        library_store.set_status(AGENT["id"], k, "indexed")

    def no_scan(*a, **kw):
        raise AssertionError("the whole index was read")
    monkeypatch.setattr(hybrid_index, "_scan", no_scan)
    monkeypatch.setattr(hybrid_index, "_table", no_scan)
    assert hybrid_index.sources(AGENT, limit=2) is None


# ── maintenance ──────────────────────────────────────────────────────────

def test_the_index_is_maintained_when_the_indexing_queue_drains(monkeypatch):
    processed, maintained = [], []
    monkeypatch.setattr(library_index, "process", lambda agent_id, key: processed.append(key))
    monkeypatch.setattr(hybrid_index, "maintain", lambda agent: maintained.append(agent["id"]))
    library_index.enqueue(AGENT["id"], ["k1", "k2", "k3"])
    library_index._queue.join()
    assert processed == ["k1", "k2", "k3"]
    assert maintained and set(maintained) == {AGENT["id"]}


# ── failed re-index ──────────────────────────────────────────────────────

@pytest.mark.parametrize("old_chunks, status", [(True, "indexed"), (False, "parsed")])
def test_a_failed_reindex_keeps_the_status_true_to_what_search_serves(monkeypatch, old_chunks, status):
    (key,) = _keys(1)
    library_store.set_status(AGENT["id"], key, "indexing")
    monkeypatch.setattr(hybrid_index, "has_document", lambda agent, k: old_chunks)

    def fail(agent, k):
        raise RuntimeError("402 RESOURCE_EXHAUSTED prepayment")
    monkeypatch.setattr(library_index, "index_document", fail)
    library_index.process(AGENT["id"], key)
    doc = library_store.get_document(AGENT["id"], key, with_parsed=False)
    assert doc["status"] == status and "קרדיט" in doc["error"]
    assert ("הגרסה הקודמת" in doc["error"]) == old_chunks


def test_a_failed_batch_job_uses_the_same_rule(monkeypatch):
    (key,) = _keys(1)
    library_store.set_status(AGENT["id"], key, "indexing")
    monkeypatch.setattr(hybrid_index, "has_document", lambda agent, k: True)
    monkeypatch.setattr(library_batch, "_jobs", lambda open_only=True: [
        {"name": "batches/1", "agent_id": AGENT["id"], "documents": [[key, 3]]}])
    monkeypatch.setattr(library_batch, "_set_job", lambda *a, **kw: None)

    class Client:
        batches = type("B", (), {"get": staticmethod(lambda name: type("J", (), {
            "state": "JOB_STATE_EXPIRED", "error": None})())})
    monkeypatch.setattr("src.embeddings.genai_client", lambda: Client())
    library_batch.poll_once()
    assert library_store.get_document(AGENT["id"], key, with_parsed=False)["status"] == "indexed"


# ── late worker results ──────────────────────────────────────────────────

def test_a_late_or_duplicate_result_is_ignored(client):
    keys = _keys(2)
    pages = [{"number": 0, "markdown": "טקסט ההכרעה " * 30}]
    library_store.set_status(AGENT["id"], keys[0], "indexed", chunks=5)      # already done
    r = client.post("/api/worker/result", json={"agent_id": AGENT["id"], "record_key": keys[0], "pages": pages})
    assert r.json() == {"status": "ignored"}
    doc = library_store.get_document(AGENT["id"], keys[0], with_parsed=False)
    assert doc["status"] == "indexed" and doc["chunks"] == 5
    # cancelled (back in the catalog) — ignored too
    r = client.post("/api/worker/result", json={"agent_id": AGENT["id"], "record_key": keys[1], "pages": pages})
    assert r.json() == {"status": "ignored"}
    # claimed, or back in the queue after the claim expired — accepted
    library_store.set_status(AGENT["id"], keys[1], "queued")
    r = client.post("/api/worker/result", json={"agent_id": AGENT["id"], "record_key": keys[1], "pages": pages})
    assert r.json() == {"status": "parsed"}


# ── restart ──────────────────────────────────────────────────────────────

def test_restart_resumes_every_interrupted_document_not_just_the_first_page(monkeypatch):
    keys = _keys(251)
    for k in keys:
        library_store.set_status(AGENT["id"], k, "indexing")
    resumed = []
    monkeypatch.setattr(library_batch, "resume", lambda: set())
    monkeypatch.setattr("src.agents.list_agents", lambda: [AGENT])
    monkeypatch.setattr(library_index, "enqueue", lambda agent_id, ks: resumed.extend(ks))
    library_index.resume_interrupted()
    assert sorted(resumed) == sorted(keys)
