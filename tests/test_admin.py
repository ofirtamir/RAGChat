"""Tests for the admin console backend: access control, quotas data, library flow."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import admin_api, auth  # noqa: E402
from src import admin_store, library_index, library_store  # noqa: E402

AGENT = {
    "id": "test_agent",
    "name": "בדיקה",
    "filters": [
        {"name": "AppraisalHeader", "label": "כותרת", "type": "text"},
        {"name": "Committee", "label": "ועדה", "type": "choice", "values": ["ירושלים", "חיפה"]},
        {"name": "Block", "label": "גוש", "type": "text"},
        {"name": "PublicityDate", "label": "פרסום", "type": "date"},
    ],
}


def record(i: int, committee: str = "ירושלים", date: str = "2025-03-01") -> dict:
    return {
        "AppraisalHeader": f"הכרעה {i}", "Committee": committee, "Block": str(6000 + i),
        "PublicityDate": f"{date}T00:00:00+03:00",
        "Document": [{"FileName": f"https://example.org/doc/{i}", "DisplayName": f"הכרעה {i}",
                      "Extension": "pdf"}],
        "DocSummary": {},
    }


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "admin.db"
    monkeypatch.setattr(admin_store, "_DB_PATH", db)
    monkeypatch.setattr(library_store, "_DB_PATH", db)
    admin_store._init_db()
    library_store._init_db()


@pytest.fixture
def indexed(monkeypatch):
    """Capture what would be sent to the background indexer / removed from the index."""
    calls = {"enqueued": [], "removed": []}
    monkeypatch.setattr(library_index, "enqueue", lambda agent_id, keys: calls["enqueued"].extend(keys))
    monkeypatch.setattr(library_index, "remove_from_index",
                        lambda agent_id, key: calls["removed"].append(key))
    return calls


@pytest.fixture
def client(monkeypatch, indexed):
    monkeypatch.setattr(admin_api, "_agent", lambda agent_id: AGENT)
    app = FastAPI()
    app.include_router(admin_api.admin)
    app.include_router(admin_api.worker)
    app.dependency_overrides[auth.require_admin] = lambda: {"email": "admin@example.org"}
    app.dependency_overrides[auth.require_worker] = lambda: None
    return TestClient(app)


# ── access control ───────────────────────────────────────────────────────

def test_admin_is_decided_by_verified_email(monkeypatch):
    monkeypatch.setattr(auth, "ADMIN_EMAILS", {"boss@example.org"})
    assert auth.is_admin({"email": "Boss@Example.org", "email_verified": True})
    assert not auth.is_admin({"email": "boss@example.org", "email_verified": False})
    assert not auth.is_admin({"email": "other@example.org", "email_verified": True})
    assert not auth.is_admin({"sub": "1"})


def test_no_admin_emails_means_no_admin(monkeypatch):
    monkeypatch.setattr(auth, "ADMIN_EMAILS", set())
    assert not auth.is_admin({"email": "", "email_verified": True})


def test_new_user_waits_for_approval_and_admin_does_not(monkeypatch):
    monkeypatch.setattr(auth, "ADMIN_EMAILS", {"boss@example.org"})
    user = auth.access_state({"sub": "u1", "email": "a@example.org", "email_verified": True})
    assert (user["allowed"], user["reason"]) == (False, "pending")
    boss = auth.access_state({"sub": "b1", "email": "boss@example.org", "email_verified": True})
    assert boss["allowed"] and boss["is_admin"] and boss["user"]["status"] == "approved"

    admin_store.update_user("u1", status="approved")
    assert auth.access_state({"sub": "u1", "email": "a@example.org"})["allowed"]
    admin_store.update_user("u1", status="blocked")
    assert auth.access_state({"sub": "u1", "email": "a@example.org"})["reason"] == "blocked"


def test_without_any_admin_configured_nobody_is_locked_out(monkeypatch):
    monkeypatch.setattr(auth, "ADMIN_EMAILS", set())
    assert auth.access_state({"sub": "u1", "email": "a@example.org", "email_verified": True})["allowed"]


def test_open_mode_lets_pending_users_in_but_not_blocked_ones(monkeypatch):
    monkeypatch.setattr(auth, "ADMIN_EMAILS", {"boss@example.org"})
    admin_store.update_settings({"require_approval": False})
    assert auth.access_state({"sub": "u1", "email": "a@example.org"})["allowed"]
    admin_store.update_user("u1", status="blocked")
    assert not auth.access_state({"sub": "u1", "email": "a@example.org"})["allowed"]


def test_admin_routes_reject_non_admin(monkeypatch):
    monkeypatch.setattr(auth, "ADMIN_EMAILS", {"boss@example.org"})
    app = FastAPI()
    app.include_router(admin_api.admin)
    app.include_router(admin_api.worker)
    app.dependency_overrides[auth.get_current_user] = lambda: {
        "sub": "u1", "email": "a@example.org", "email_verified": True}
    c = TestClient(app)
    assert c.get("/api/admin/users").status_code == 403
    assert c.get("/api/admin/settings").status_code == 403
    # worker routes: no key configured -> closed; wrong key -> rejected
    monkeypatch.setattr(auth, "_WORKER_API_KEY", "")
    assert c.post("/api/worker/claim", json={"agent_id": "x"}).status_code == 503
    monkeypatch.setattr(auth, "_WORKER_API_KEY", "secret")
    assert c.post("/api/worker/claim", json={"agent_id": "x"},
                  headers={"X-Worker-Key": "nope"}).status_code == 401


# ── usage and settings ───────────────────────────────────────────────────

def test_usage_is_counted_per_user_and_costed_from_settings():
    admin_store.touch_user("u1", "a@example.org", "א", None)
    admin_store.update_settings({"price_input_per_m": 1.0, "price_output_per_m": 10.0})
    admin_store.record_usage("u1", "chat", "m", input_tokens=1_000_000, output_tokens=100_000)
    admin_store.record_usage("u1", "chat", "m", input_tokens=0, output_tokens=0)
    admin_store.record_usage(None, "indexing", "embedding", 500, 0, cost_usd=0.25)

    assert admin_store.questions_today("u1") == 2
    assert admin_store.cost_this_month() == pytest.approx(2.25)
    summary = admin_store.usage_summary(days=7)
    assert summary["questions_today"] == 2 and len(summary["daily"]) == 7
    assert summary["avg_cost_per_question"] == pytest.approx(1.0)
    assert summary["daily"][-1]["indexing_cost"] == pytest.approx(0.25)
    row = admin_store.list_users()[0]
    assert (row["questions_today"], row["questions_month"]) == (2, 2)
    assert row["cost_month"] == pytest.approx(2.0)


def test_settings_ignore_unknown_keys_and_negative_values():
    out = admin_store.update_settings({"default_daily_limit": -5, "hack": 1, "monthly_budget_usd": "12.5"})
    assert out["default_daily_limit"] == 0 and out["monthly_budget_usd"] == 12.5 and "hack" not in out


def test_user_patch_endpoint(client):
    admin_store.touch_user("u1", "a@example.org", "א", None)
    r = client.patch("/api/admin/users/u1", json={"status": "approved", "daily_limit": 5})
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["daily_limit"] == 5
    assert client.patch("/api/admin/users/u1", json={"clear_daily_limit": True}).json()["daily_limit"] is None
    assert client.patch("/api/admin/users/u1", json={"status": "king"}).status_code == 400
    assert client.patch("/api/admin/users/nobody", json={"status": "approved"}).status_code == 404


# ── library flow ─────────────────────────────────────────────────────────

def test_catalog_upload_is_idempotent_and_searchable(client):
    records = [record(1), record(2, committee="חיפה", date="2024-01-15"), record(3)]
    assert client.post("/api/worker/catalog", json={"agent_id": "test_agent", "records": records}).json() == {
        "received": 3, "added": 3}
    assert client.post("/api/worker/catalog", json={"agent_id": "test_agent", "records": records}).json()["added"] == 0

    def search(**body):
        return client.post("/api/admin/library/search", json={"agent_id": "test_agent", **body}).json()

    assert search()["total"] == 3 and search()["counts"]["catalog"] == 3
    assert search(filters={"Committee": "חיפה"})["total"] == 1
    assert search(filters={"PublicityDate": {"from": "2025-01-01"}})["total"] == 2
    assert search(filters={"Block": "6002"})["documents"][0]["title"] == "הכרעה 2"
    assert search(q="הכרעה 3")["total"] == 1
    assert search(filters={"Nope": "x", "Committee": ""})["total"] == 3


def test_queue_parse_review_then_index(client, indexed):
    client.post("/api/worker/catalog", json={"agent_id": "test_agent", "records": [record(1), record(2)]})
    key = library_store.search(AGENT, q="הכרעה 1")["documents"][0]["record_key"]

    act = lambda action, **kw: client.post(  # noqa: E731
        "/api/admin/library/action", json={"agent_id": "test_agent", "action": action, **kw}).json()
    assert act("queue", keys=[key])["counts"]["queued"] == 1

    claimed = client.post("/api/worker/claim", json={"agent_id": "test_agent", "limit": 5}).json()["documents"]
    assert [d["record_key"] for d in claimed] == [key]
    assert claimed[0]["files"][0]["url"] == "https://example.org/doc/1"
    assert client.post("/api/worker/claim", json={"agent_id": "test_agent"}).json()["documents"] == []
    assert library_store.worker_status()["online"]

    pages = [{"number": 0, "markdown": "טקסט ההכרעה " * 30},
             {"number": 1, "markdown": "| א | ב |\n|---|---|\n| 1 | 2 |"}]
    r = client.post("/api/worker/result", json={
        "agent_id": "test_agent", "record_key": key, "pages": pages, "tables": 1,
        "problems": [{"page": 1, "problems": ["word coverage 90%"]}]})
    assert r.json() == {"status": "parsed"} and indexed["enqueued"] == []

    doc = client.get(f"/api/admin/library/test_agent/{key}").json()
    assert doc["status"] == "parsed" and doc["pages"] == 2 and doc["flagged_tables"] == 1
    assert doc["parsed_pages"] == pages and doc["problems"][0]["page"] == 1

    assert act("index", keys=[key])["counts"]["indexing"] == 1
    assert indexed["enqueued"] == [key]
    library_store.set_status("test_agent", key, "indexed", chunks=4)
    assert library_store.status_counts("test_agent")["indexed_chunks"] == 4
    assert act("unindex", keys=[key])["counts"]["parsed"] == 1 and indexed["removed"] == [key]


def test_queue_and_index_everything_matching_a_filter(client, indexed):
    client.post("/api/worker/catalog", json={"agent_id": "test_agent", "records": [
        record(1), record(2, committee="חיפה"), record(3, committee="חיפה")]})
    r = client.post("/api/admin/library/action", json={
        "agent_id": "test_agent", "action": "queue_and_index",
        "selection": {"filters": {"Committee": "חיפה"}}}).json()
    assert r["moved"] == 2 and r["counts"]["queued"] == 2 and r["counts"]["catalog"] == 1

    doc = client.post("/api/worker/claim", json={"agent_id": "test_agent", "limit": 1}).json()["documents"][0]
    r = client.post("/api/worker/result", json={
        "agent_id": "test_agent", "record_key": doc["record_key"],
        "pages": [{"number": 0, "markdown": "תוכן " * 50}]})
    assert r.json() == {"status": "indexing"} and indexed["enqueued"] == [doc["record_key"]]


def test_worker_requeues_documents_whose_pictures_were_read(client):
    client.post("/api/worker/catalog", json={"agent_id": "test_agent", "records": [record(1), record(2), record(3)]})
    keys = [d["record_key"] for d in library_store.search(AGENT)["documents"]]
    library_store.set_status("test_agent", keys[0], "indexed")
    library_store.set_status("test_agent", keys[1], "parsed")    # keys[2] stays in the catalog
    r = client.post("/api/worker/requeue", json={"agent_id": "test_agent", "keys": keys})
    assert r.json() == {"queued": 2}
    indexed, parsed, untouched = (library_store.get_document("test_agent", k) for k in keys)
    assert indexed["status"] == "queued" and indexed["auto_index"]          # re-indexed after the new parse
    assert parsed["status"] == "queued" and not parsed["auto_index"]
    assert untouched["status"] == "catalog"
    assert client.post("/api/worker/requeue", json={"agent_id": "test_agent", "keys": []}).status_code == 422


def test_scanned_and_failed_documents_are_marked(client):
    client.post("/api/worker/catalog", json={"agent_id": "test_agent", "records": [record(1), record(2)]})
    client.post("/api/admin/library/action", json={
        "agent_id": "test_agent", "action": "queue", "selection": {}})
    a, b = client.post("/api/worker/claim", json={"agent_id": "test_agent"}).json()["documents"]
    assert client.post("/api/worker/result", json={
        "agent_id": "test_agent", "record_key": a["record_key"], "pages": [{"number": 0, "markdown": ""}],
    }).json() == {"status": "no_text"}
    assert client.post("/api/worker/result", json={
        "agent_id": "test_agent", "record_key": b["record_key"], "ok": False, "error": "HTTP 500",
    }).json() == {"status": "failed"}
    assert library_store.get_document("test_agent", b["record_key"])["error"] == "HTTP 500"
    counts = library_store.status_counts("test_agent")["counts"]
    assert counts["no_text"] == 1 and counts["failed"] == 1


def test_chunks_carry_filter_metadata_and_page():
    library_store.upsert_catalog(AGENT, [record(1)])
    key = library_store.search(AGENT)["documents"][0]["record_key"]
    library_store.save_parsed("test_agent", key, [
        {"number": 0, "markdown": "פסקה ראשונה."}, {"number": 1, "markdown": ""},
        {"number": 2, "markdown": "פסקה בעמוד שלישי."}], tables=0, problems=[])
    doc = library_store.get_document("test_agent", key)
    chunks = library_index.build_chunks(doc)
    # short paragraphs from different pages are packed together; the chunk keeps its first page
    assert [c.page for c in chunks] == [0]
    assert "פסקה ראשונה." in chunks[0].text and "פסקה בעמוד שלישי." in chunks[0].text
    meta = library_index.document_meta(AGENT, doc)
    assert meta["Committee"] == "ירושלים" and meta["PublicityDate_int"] == 20250301
    assert meta["record_key"] == key and meta["source"] == "הכרעה 1"


def test_failed_indexing_keeps_the_parsed_text_and_can_be_retried(monkeypatch):
    import src.agents

    library_store.upsert_catalog(AGENT, [record(1)])
    key = library_store.search(AGENT)["documents"][0]["record_key"]
    library_store.save_parsed("test_agent", key, [{"number": 0, "markdown": "טקסט " * 40}], 0, [])
    library_store.set_status("test_agent", key, "indexing")
    monkeypatch.setattr(src.agents, "get_agent", lambda agent_id: AGENT)

    def out_of_credit(agent, key):
        raise RuntimeError("402 RESOURCE_EXHAUSTED: Your prepayment credits are depleted")

    monkeypatch.setattr(library_index, "index_document", out_of_credit)
    library_index.process("test_agent", key)
    doc = library_store.get_document("test_agent", key)
    assert doc["status"] == "parsed" and "Gemini" in doc["error"] and doc["parsed_pages"]

    def indexed(agent, key):  # what a successful index_document records
        library_store.set_status("test_agent", key, "indexed", chunks=7, embed_tokens=20_000,
                                 index_cost_usd=0.003)

    monkeypatch.setattr(library_index, "index_document", indexed)
    library_store.transition(AGENT, "indexing", ("parsed", "indexed"), keys=[key])
    library_index.process("test_agent", key)
    doc = library_store.get_document("test_agent", key)
    assert (doc["status"], doc["chunks"], doc["error"]) == ("indexed", 7, None)
    assert (doc["embed_tokens"], doc["index_cost_usd"]) == (20_000, 0.003)
    cost = library_store.status_counts("test_agent")["index_cost"]
    assert cost["documents"] == 1 and cost["total_usd"] == pytest.approx(0.003)
    assert cost["projected_catalog_usd"] == pytest.approx(0.003)  # one document in this catalog


def test_indexing_cost_uses_gemini_token_count(monkeypatch):
    import src.embeddings

    library_store.upsert_catalog(AGENT, [record(1)])
    key = library_store.search(AGENT)["documents"][0]["record_key"]
    library_store.save_parsed("test_agent", key, [{"number": 0, "markdown": "טקסט " * 40}], 0, [])

    written = {}
    monkeypatch.setattr(library_index.hybrid_index, "replace_document",
                        lambda agent, key, rows: written.update(rows=rows))
    monkeypatch.setattr(src.embeddings, "embed_documents", lambda texts: [[0.0] * 768 for _ in texts])
    monkeypatch.setattr(src.embeddings, "count_embedding_tokens", lambda texts: (123_456, True))
    admin_store.update_settings({"price_embedding_per_m": 0.15})

    result = library_index.index_document(AGENT, key)
    assert result == {"chunks": len(written["rows"]), "tokens": 123_456,
                      "cost_usd": pytest.approx(123_456 * 0.15 / 1_000_000), "exact": True}
    assert admin_store.cost_this_month() == pytest.approx(123_456 * 0.15 / 1_000_000)


def test_token_count_falls_back_to_an_estimate(monkeypatch):
    import src.embeddings

    def offline(*args, **kwargs):
        raise ConnectionError("no network")

    monkeypatch.setattr(src.embeddings.requests, "post", offline)
    tokens, exact = src.embeddings.count_embedding_tokens(["א" * 160])
    assert (tokens, exact) == (100, False)
