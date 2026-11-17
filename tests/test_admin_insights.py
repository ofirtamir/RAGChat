"""Tests for the admin quality tools: question log, feedback, chunk viewer, analytics, exports."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import admin_api, admin_insights_api, auth, feedback_api  # noqa: E402
from src import admin_insights, admin_store, chat_log, chunk_quality, library_store  # noqa: E402
from src.govil_client import record_key  # noqa: E402
from tests.test_admin import AGENT, record  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "admin.db"
    for module in (admin_store, library_store, chat_log, admin_insights):
        monkeypatch.setattr(module, "_DB_PATH", db)
    admin_store._init_db()
    library_store._init_db()
    chat_log._init_db()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(admin_api, "_agent", lambda agent_id: AGENT)
    monkeypatch.setattr(admin_insights_api, "_agent", lambda agent_id: AGENT)
    app = FastAPI()
    app.include_router(admin_insights_api.router)
    app.dependency_overrides[auth.require_admin] = lambda: {"email": "admin@example.org"}
    return TestClient(app)


def usage(cost=0.01, inp=1000, out=200):
    return SimpleNamespace(input_tokens=inp, output_tokens=out, llm_calls=3, cost_usd=cost, models={"m"})


def result(answer="תשובה [1]", needs_retrieval=True, full_doc=False, retrieval=None):
    return {
        "answer": answer,
        "route": SimpleNamespace(needs_retrieval=needs_retrieval),
        "full_doc_decision": SimpleNamespace(needs_full_document=full_doc),
        "rewritten": SimpleNamespace(rewritten_queries=["היטל השבחה"]),
        "retrieval": [{"record_key": "k1", "source": "הכרעה 1", "page": 2, "section": "סעיף 1",
                       "chunk_index": 0, "score": 0.03, "rerank": 8}] if retrieval is None else retrieval,
        "citations": [],
    }


def log(qid, user="u1", **kw):
    args = dict(question_id=qid, user_sub=user, session_id="s", agent_id="test_agent", query="מה ההיטל?",
                filters={}, result=result(), latency_ms=4000, first_token_ms=1500, usage=usage(), ok=True)
    args.update(kw)
    chat_log.log_question(**args)


# ── question log ─────────────────────────────────────────────────────────

def test_question_is_logged_with_route_retrieval_and_answer():
    admin_store.touch_user("u1", "a@example.org", "א", None)
    log("q1")
    row = chat_log.get_question("q1")
    assert row["route"] == "retrieval" and row["retrieved"] == 1
    assert row["answer"] == "תשובה [1]" and row["search_queries"] == ["היטל השבחה"]
    assert row["retrieval"][0]["rerank"] == 8 and row["email"] == "a@example.org"


def test_routes_and_no_answer_detection():
    log("chat", result=result(needs_retrieval=False))
    log("full", result=result(full_doc=True))
    log("empty", result=result(answer="לא מצאתי במסמכים התייחסות לכך", retrieval=[]))
    log("gap", result=result(answer="לא מצאתי במסמכים התייחסות לשאלה"))
    log("err", result=None, ok=False, error="boom")
    assert chat_log.get_question("chat")["route"] == "chitchat"
    assert chat_log.get_question("full")["route"] == "full_doc"
    assert chat_log.get_question("empty")["route"] == "no_docs"
    assert chat_log.get_question("gap")["no_answer"] == 1
    assert chat_log.get_question("chat")["no_answer"] == 0
    assert chat_log.get_question("err")["error"] == "boom"
    assert chat_log.list_questions(flag="no_answer")["total"] == 1
    assert chat_log.list_questions(flag="error")["total"] == 1
    assert chat_log.list_questions(flag="no_sources")["total"] == 1


def test_only_the_asker_can_rate_and_rating_can_be_withdrawn():
    log("q1", user="u1")
    assert not chat_log.set_feedback("q1", "someone-else", -1, "x")
    assert chat_log.set_feedback("q1", "u1", -1, "  התשובה שגויה  ")
    row = chat_log.get_question("q1")
    assert (row["rating"], row["comment"]) == (-1, "התשובה שגויה")
    assert chat_log.list_questions(flag="down")["total"] == 1
    chat_log.set_feedback("q1", "u1", 0, None)
    assert chat_log.get_question("q1")["rating"] is None


def test_quality_summary_counts_feedback_latency_and_documents():
    for i in range(4):
        log(f"q{i}", latency_ms=1000 * (i + 1))
    chat_log.set_feedback("q0", "u1", 1, None)
    chat_log.set_feedback("q1", "u1", -1, None)
    chat_log.record_refusal("u2", "daily_limit")
    s = chat_log.quality_summary(days=7)
    assert s["questions"] == 4 and s["up"] == 1 and s["down"] == 1 and s["satisfaction"] == 0.5
    assert s["latency_ms"]["p50"] in (2000, 3000)
    assert s["top_documents"][0] == {"record_key": "k1", "title": "הכרעה 1", "questions": 4, "down": 1}
    assert s["refusals"][0]["reason"] == "daily_limit" and len(s["daily"]) == 7


# ── chunk quality ────────────────────────────────────────────────────────

def test_chunk_flags():
    chunks, summary = chunk_quality.analyse([
        {"text": "[סעיף 1: רקע]\n" + "טקסט רגיל ומלא. " * 20, "section": "סעיף 1: רקע", "page": 0},
        {"text": "קצר", "section": "", "page": 0},
        {"text": "קצר", "section": "", "page": 1},
        {"text": "àáâãäåæçèé" * 20, "section": "סעיף 2", "page": 1},
        {"text": "[סעיף 3]\n| א | ב |\n|---|---|\n| 1 | 2 |\n" + "שורה. " * 40, "section": "סעיף 3", "page": 2},
        {"text": "[סעיף 4]\n| א | ב |\n|---|---|\n| 1 | 2 |", "section": "סעיף 4", "page": 3},
    ])
    assert chunks[0]["flags"] == []
    assert set(chunks[1]["flags"]) == {"tiny", "no_section", "duplicate"}
    assert "garbled" in chunks[3]["flags"] and "table" in chunks[4]["flags"]
    assert chunks[5]["flags"] == ["table"]  # a small whole table is not "tiny"
    assert summary["chunks"] == 6 and summary["with_problems"] == 3 and summary["sections"] == 4


def test_chunks_endpoint_previews_a_parsed_document(client):
    library_store.upsert_catalog(AGENT, [record(1)])
    key = record_key(record(1))
    library_store.save_parsed("test_agent", key, [
        {"number": 0, "markdown": "1. מטרת ההחלטה\n\n" + "ההחלטה עוסקת בהיטל השבחה. " * 30},
        {"number": 1, "markdown": "2. הרקע\n\nתוכנית חדשה אושרה בשנת 2020 בגוש 6001."},
    ], 0, [])
    body = client.get(f"/api/admin/library/test_agent/{key}/chunks").json()
    assert body["source"] == "preview" and body["summary"]["chunks"] == len(body["chunks"]) >= 2
    assert {c["section"] for c in body["chunks"]} >= {"סעיף 1: מטרת ההחלטה", "סעיף 2: הרקע"}
    assert all("flags" in c and c["tokens_est"] > 0 for c in body["chunks"])


def test_library_chunk_quality_aggregates_indexed_documents(client):
    library_store.upsert_catalog(AGENT, [record(1), record(2)])
    for i in (1, 2):
        key = record_key(record(i))
        library_store.save_parsed("test_agent", key, [{"number": 0, "markdown": "טקסט " * 40}], 0, [])
        library_store.set_status("test_agent", key, "indexed", chunks=4, chunk_stats={
            "chunks": 4, "chars": 2000, "with_problems": i, "flags": {"tiny": i},
            "size_buckets": [0, 4, 0, 0, 0, 0]})
    q = client.get("/api/admin/chunk-quality/test_agent").json()
    assert q["measured"] == 2 and q["chunks"] == 8 and q["flags"]["tiny"] == 3
    assert q["worst_documents"][0]["with_problems"] == 2


# ── analytics, users, exports ────────────────────────────────────────────

def test_engagement_segments_and_costs():
    for sub, status in (("u1", "approved"), ("u2", "approved"), ("u3", "approved"), ("u4", "pending")):
        admin_store.touch_user(sub, f"{sub}@example.org", sub, None)
        admin_store.update_user(sub, status=status)
    for _ in range(admin_insights.POWER_USER_QUESTIONS):
        admin_store.record_usage("u1", "chat", "m", 100, 10, cost_usd=0.01)
    admin_store.record_usage("u2", "chat", "m", 100, 10, cost_usd=0.03)
    admin_store.record_usage(None, "indexing", "embedding", 500, 0, cost_usd=0.5)

    e = admin_insights.engagement()
    assert (e["dau"], e["wau"], e["mau"]) == (2, 2, 2)
    assert e["segments"]["power"]["count"] == 1 and e["segments"]["regular"]["count"] == 1
    assert e["segments"]["never"]["count"] == 1  # u3: approved, never asked; u4 is pending
    assert e["cohorts"][-1]["size"] == 2 and e["cohorts"][-1]["retention"] == [1.0]

    c = admin_insights.costs(days=30)
    assert c["per_question"]["count"] == 21
    assert c["unit"]["active_users"] == 2 and c["unit"]["active_share"] == pytest.approx(2 / 3)
    assert {k["kind"] for k in c["by_kind"]} == {"chat", "indexing"}


def test_user_activity_bulk_status_and_exports(client):
    for sub in ("u1", "u2"):
        admin_store.touch_user(sub, f"{sub}@example.org", "שם", None)
    log("q1", user="u1")
    act = client.get("/api/admin/users/u1/activity").json()
    assert act["questions"] == 1 and act["recent"][0]["id"] == "q1" and len(act["daily"]) == 30
    assert "cost_month" in act["user"] and "questions_month" in act["user"]

    assert client.post("/api/admin/users/bulk", json={"subs": ["u1", "u2", "nobody"],
                                                      "status": "approved"}).json() == {"updated": 2}
    assert client.post("/api/admin/users/bulk", json={"subs": ["u1"], "status": "x"}).status_code == 400
    assert admin_store.get_user("u2")["status"] == "approved"

    csv_text = client.get("/api/admin/export/questions.csv").content.decode("utf-8-sig")
    assert csv_text.splitlines()[0].startswith("ts,email") and "מה ההיטל?" in csv_text
    assert "u2@example.org" in client.get("/api/admin/export/users.csv").content.decode("utf-8-sig")
    assert client.get("/api/admin/questions?flag=bogus").status_code == 400


def test_feedback_endpoint_only_rates_own_questions():
    log("q1", user="u1")
    app = FastAPI()
    app.include_router(feedback_api.router)
    app.dependency_overrides[auth.get_active_user_id] = lambda: "u1"
    c = TestClient(app)
    assert c.post("/api/questions/q1/feedback", json={"rating": -1, "comment": "חסר"}).json() == {"status": "ok"}
    assert chat_log.get_question("q1")["comment"] == "חסר"
    assert c.post("/api/questions/q1/feedback", json={"rating": 5}).status_code == 400
    app.dependency_overrides[auth.get_active_user_id] = lambda: "u2"
    assert c.post("/api/questions/q1/feedback", json={"rating": 1}).status_code == 404
