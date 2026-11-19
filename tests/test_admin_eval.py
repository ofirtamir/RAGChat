"""Admin retrieval benchmark: the store, the /api/admin/eval routes and the background
jobs. Search, sampling and the question-writing model are replaced — no network, no Gemini."""
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import admin_eval_api, auth  # noqa: E402
from src import (admin_store, chat_log, eval_jobs, eval_questions, eval_search, eval_store,  # noqa: E402
                 library_store)
from src.govil_client import record_key  # noqa: E402
from tests.test_admin import AGENT, record  # noqa: E402

K1, K2, K3 = (record_key(record(i)) for i in (1, 2, 3))


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "admin.db"
    for module in (admin_store, library_store, chat_log, eval_store):
        monkeypatch.setattr(module, "_DB_PATH", db)
    admin_store._init_db()
    library_store._init_db()
    chat_log._init_db()
    eval_store._init_db()
    library_store.upsert_catalog(AGENT, [record(1), record(2), record(3)])
    yield
    eval_jobs.wait(10)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(admin_eval_api, "_agent", lambda agent_id: AGENT)
    app = FastAPI()
    app.include_router(admin_eval_api.router)
    app.dependency_overrides[auth.require_admin] = lambda: {"email": "admin@example.org"}
    return TestClient(app)


def ranked(*keys):
    return [{"rank": i + 1, "id": f"{k}_{i}", "record_key": k, "section": f"סעיף {i}"} for i, k in enumerate(keys)]


def fake_search(rankings: dict, errors: dict | None = None):
    """search_one stand-in: the ranking for each question text; no index, no embeddings."""
    def search_one(agent, query, filters=None, mode="hybrid", k=20, with_meta=True, usage=None):
        out = {"query": query, "results": ranked(*rankings.get(query, [])), "errors": {}, "documents": {}}
        if errors and query in errors:
            out["error"] = errors[query]
        return out
    return search_one


def labelled_set(questions: dict) -> dict:
    """A set whose questions (text -> gold keys) are already labelled."""
    qset = eval_store.create_set("test_agent", "בדיקה", "manual")
    eval_store.add_questions(qset["id"], [{"question": q, "type": "principle", "gold": g}
                                          for q, g in questions.items()], "manual")
    return qset


def run_job(client, set_id, label, **extra):
    resp = client.post("/api/admin/eval/runs", json={"agent_id": "test_agent", "set_id": set_id,
                                                     "label": label, "confirm": True, **extra})
    assert resp.status_code == 200, resp.text
    eval_jobs.wait(10)
    return resp.json()


# ── store ────────────────────────────────────────────────────────────────

def test_store_keeps_questions_labels_and_filters():
    qset = eval_store.create_set("test_agent", "סט", "synthetic")
    items = [{"question": "מה מקדם הדחייה?", "type": "principle", "gold": [K1]},
             {"question": "גוש 6001 - מה נקבע?", "type": "block_plot", "gold": [K1], "expected_meta": {"Block": "6001"}},
             {"question": "  מה   מקדם הדחייה? ", "type": "principle"}]
    assert eval_store.add_questions(qset["id"], items, "synthetic") == 2  # the third repeats the first
    real = eval_store.real_set("test_agent")
    assert eval_store.real_set("test_agent")["id"] == real["id"]
    eval_store.add_questions(real["id"], [{"question": "שאלה אמיתית", "type": "real"}], "real")
    assert len(eval_store.scorable_questions(real["id"])) == 0

    q = eval_store.list_questions(real["id"], unlabelled=True)["questions"][0]
    assert eval_store.update_question(q["id"], gold=[K2, K2, ""], qtype="table")["gold"] == [K2]
    assert len(eval_store.scorable_questions(real["id"])) == 1
    assert eval_store.update_question(q["id"], gold=[])["gold"] == []

    page = eval_store.list_questions(qset["id"], qtype="block_plot")
    assert page["total"] == 1 and page["questions"][0]["expected_meta"] == {"Block": "6001"}
    assert eval_store.list_questions(qset["id"], q="דחייה")["total"] == 1
    assert eval_store.facets(qset["id"]) == {"types": {"principle": 1, "block_plot": 1},
                                             "sources": {"synthetic": 2}}
    sets = {s["name"]: s for s in eval_store.list_sets("test_agent")}
    assert sets["סט"]["questions"] == 2 and sets["סט"]["labelled"] == 2 and sets["שאלות אמיתיות"]["labelled"] == 0
    assert eval_store.delete_question(page["questions"][0]["id"])
    assert eval_store.delete_set(qset["id"]) and eval_store.get_set(qset["id"]) is None


def test_restart_marks_running_work_failed():
    qset = eval_store.create_set("test_agent", "בבנייה", "synthetic", status="building")
    run = eval_store.create_run("test_agent", qset["id"], "hybrid", "x", 20, 5, 0.0)
    eval_store.fail_interrupted()
    assert eval_store.get_run(run["id"])["status"] == "failed"
    assert eval_store.get_run(run["id"])["error"] == eval_store.INTERRUPTED
    assert eval_store.get_set(qset["id"])["status"] == "partial"


# ── building a set ───────────────────────────────────────────────────────

CHUNKS = [
    {"id": f"c{i}", "record_key": key, "section": "סעיף 2", "text": text, "meta": meta}
    for i, (key, text, meta) in enumerate([
        (K1, "השמאי קבע כי מקדם הדחייה יעמוד על 0.9 בשל ההמתנה הארוכה למימוש הזכויות במקרקעין", {}),
        (K2, "| שטח | מחיר |\n|---|---|\n| 100 | 5000 |\n| 200 | 9000 |", {}),
        (K3, "נקבע כי שווי הקרקע ייגזר מעסקאות השוואה באזור", {"Block": "6003", "Plot": "12"}),
    ])
]


def test_build_returns_the_estimate_first_and_writes_only_after_confirm(client, monkeypatch):
    calls = []

    def ask(prompt):
        calls.append(prompt)
        if "0.9" in prompt:  # this one copies the chunk word for word: dropped
            return {"answerable": True, "question": "קבע כי מקדם הדחייה יעמוד על 0.9 בשל ההמתנה"}, 0.001
        return {"answerable": True, "question": "איך נקבע המחיר למטר בטבלת השטחים?"}, 0.001

    monkeypatch.setattr(eval_search, "sample_chunks", lambda agent, n, seed, **kw: [dict(c) for c in CHUNKS])
    monkeypatch.setattr(eval_questions, "ask_gemini", ask)
    body = {"agent_id": "test_agent", "n": 20}
    estimate = client.post("/api/admin/eval/sets/build", json=body).json()["estimate"]
    assert estimate["sampled"] == 3 and estimate["meta_questions"] == 1 and estimate["llm_questions"] == 2
    assert estimate["cost_usd"] > 0 and not calls and eval_store.list_sets("test_agent") == []

    started = client.post("/api/admin/eval/sets/build", json={**body, "confirm": True, "name": "ניסיון"}).json()
    eval_jobs.wait(10)
    qset = eval_store.get_set(started["set"]["id"])
    assert qset["status"] == "ready" and qset["name"] == "ניסיון" and qset["info"]["cost_usd"] == pytest.approx(0.002)
    rows = client.get(f"/api/admin/eval/sets/{qset['id']}/questions").json()
    assert rows["total"] == 2 and rows["facets"]["types"] == {"block_plot": 1, "table": 1}
    table = next(q for q in rows["questions"] if q["type"] == "table")
    assert table["gold"] == [{"record_key": K2, "title": "הכרעה 2"}] and table["source"] == "synthetic"
    job = client.get("/api/admin/eval/job").json()
    assert not job["running"] and job["error"] is None and job["done"] == 2


def test_build_stops_on_a_billing_error_and_keeps_what_it_has(client, monkeypatch):
    def broken(prompt):
        raise RuntimeError("402 Payment Required: prepaid credits exhausted")

    monkeypatch.setattr(eval_search, "sample_chunks", lambda agent, n, seed, **kw: [dict(c) for c in CHUNKS])
    monkeypatch.setattr(eval_questions, "ask_gemini", broken)
    started = client.post("/api/admin/eval/sets/build",
                          json={"agent_id": "test_agent", "n": 20, "confirm": True}).json()
    eval_jobs.wait(10)
    qset = eval_store.get_set(started["set"]["id"])
    assert qset["status"] == "partial" and "402" in qset["info"]["error"]
    assert eval_store.list_questions(qset["id"])["total"] == 1  # the metadata question
    job = client.get("/api/admin/eval/job").json()
    assert not job["running"] and "402" in job["error"]


# ── runs ─────────────────────────────────────────────────────────────────

def test_hybrid_run_scores_every_labelled_question_and_stores_results(client, monkeypatch):
    qset = labelled_set({"שאלה א": [K1], "שאלה ב": [K2], "שאלה ג": [K3]})
    eval_store.add_questions(qset["id"], [{"question": "בלי תווית"}], "manual")
    monkeypatch.setattr(eval_search, "search_one", fake_search({
        "שאלה א": [K1, K2], "שאלה ב": [K3, K3, K2], "שאלה ג": [K1, K2]}))
    run = run_job(client, qset["id"], "בסיס")["run"]

    done = client.get(f"/api/admin/eval/runs/{run['id']}").json()
    assert done["status"] == "done" and done["n_questions"] == 3 and done["done"] == 3
    assert done["summary"]["all"]["recall@10"] == pytest.approx(2 / 3, abs=1e-3)
    assert done["summary"]["all"]["hit@1"] == pytest.approx(1 / 3, abs=1e-3)
    assert 0 < done["cost_estimate"] < 0.001 and done["cost_usd"] == pytest.approx(done["cost_estimate"])
    assert done["set_name"] == "בדיקה" and eval_store.total_spent() == pytest.approx(done["cost_usd"])

    page = client.get(f"/api/admin/eval/sets/{qset['id']}/questions", params={"run_id": run["id"]}).json()
    by_text = {q["question"]: q for q in page["questions"]}
    assert by_text["שאלה ב"]["result"]["found_rank"] == 3 and by_text["שאלה ג"]["result"]["found_rank"] is None
    assert by_text["שאלה א"]["result"]["top"][1] == {"record_key": K2, "section": "סעיף 1", "rank": 2,
                                                     "title": "הכרעה 2"}
    assert by_text["בלי תווית"]["result"] is None
    missed = client.get(f"/api/admin/eval/sets/{qset['id']}/questions",
                        params={"run_id": run["id"], "missed": True}).json()
    assert [q["question"] for q in missed["questions"]] == ["שאלה ג"]
    assert [r["id"] for r in client.get("/api/admin/eval/runs", params={"agent_id": "test_agent"}).json()["runs"]] \
        == [run["id"]]


def test_pipeline_run_needs_confirm_and_stops_on_a_billing_error(client, monkeypatch):
    qset = labelled_set({"שאלה א": [K1], "שאלה ב": [K2]})
    monkeypatch.setattr(eval_search, "search_one", fake_search(
        {"שאלה א": [K1], "שאלה ב": [K2]},
        errors={"שאלה א": "analyzer: 402 prepaid credit", "שאלה ב": "analyzer: 402 prepaid credit"}))
    body = {"agent_id": "test_agent", "set_id": qset["id"], "mode": "pipeline"}
    first = client.post("/api/admin/eval/runs", json=body).json()
    assert "run" not in first and first["estimate"]["questions"] == 2 and first["estimate"]["cost_usd"] > 0
    assert first["budget"]["budget_usd"] == 1.0
    assert eval_store.list_runs("test_agent") == []

    run = run_job(client, qset["id"], "צנרת", mode="pipeline", confirm=True)["run"]
    failed = eval_store.get_run(run["id"])
    assert failed["status"] == "failed" and "402" in failed["error"]
    assert "402" in client.get("/api/admin/eval/job").json()["error"]


def test_compare_shows_metric_deltas_and_questions_that_flipped(client, monkeypatch):
    qset = labelled_set({"שאלה א": [K1], "שאלה ב": [K2], "שאלה ג": [K3]})
    monkeypatch.setattr(eval_search, "search_one", fake_search({"שאלה א": [K1], "שאלה ב": [K2]}))
    a = run_job(client, qset["id"], "לפני")["run"]
    monkeypatch.setattr(eval_search, "search_one", fake_search({"שאלה א": [K1], "שאלה ג": [K2, K3]}))
    b = run_job(client, qset["id"], "אחרי")["run"]

    cmp = client.get("/api/admin/eval/compare", params={"a": a["id"], "b": b["id"]}).json()
    assert cmp["same_set"] and cmp["shared"] == 3 and cmp["a"]["label"] == "לפני"
    assert cmp["deltas"]["all"]["recall@10"]["delta"] == 0
    assert cmp["deltas"]["all"]["mrr@20"]["delta"] == pytest.approx(-1 / 6, abs=1e-3)
    assert [r["question"] for r in cmp["gained"]] == ["שאלה ג"] and cmp["gained"][0]["rank_b"] == 2
    assert [r["question"] for r in cmp["lost"]] == ["שאלה ב"] and cmp["lost"][0]["rank_a"] == 1
    assert client.get("/api/admin/eval/compare", params={"a": a["id"], "b": "nope"}).status_code == 404
    assert client.get("/api/admin/eval/compare", params={"a": a["id"], "b": b["id"], "metric": "x"}).status_code == 400


def test_only_one_job_runs_at_a_time_and_it_can_be_cancelled(client, monkeypatch):
    qset = labelled_set({f"שאלה {i}": [K1] for i in range(eval_jobs.BATCH + 5)})
    release = threading.Event()

    def slow(agent, query, filters=None, mode="hybrid", k=20, with_meta=True, usage=None):
        release.wait(5)
        return {"results": ranked(K1)}

    monkeypatch.setattr(eval_search, "search_one", slow)
    monkeypatch.setattr(eval_search, "sample_chunks", lambda agent, n, seed, **kw: [dict(c) for c in CHUNKS])
    body = {"agent_id": "test_agent", "set_id": qset["id"], "confirm": True}
    first = client.post("/api/admin/eval/runs", json=body)
    assert first.status_code == 200 and client.get("/api/admin/eval/job").json()["running"]
    assert client.post("/api/admin/eval/runs", json=body).status_code == 409
    assert client.post("/api/admin/eval/sets/build",
                       json={"agent_id": "test_agent", "n": 20, "confirm": True}).status_code == 409
    assert [s["name"] for s in eval_store.list_sets("test_agent")] == ["בדיקה"]
    assert client.post("/api/admin/eval/job/cancel").json() == {"cancelled": True}
    release.set()
    eval_jobs.wait(10)
    run = eval_store.get_run(first.json()["run"]["id"])
    assert run["status"] == "failed" and run["done"] == eval_jobs.BATCH and run["error"] == "נעצר ידנית"
    assert len(eval_store.list_runs("test_agent")) == 1  # the refused start left no run behind


def test_run_refuses_a_set_without_labels(client):
    qset = eval_store.real_set("test_agent")
    eval_store.add_questions(qset["id"], [{"question": "שאלה בלי תווית"}], "real")
    resp = client.post("/api/admin/eval/runs", json={"agent_id": "test_agent", "set_id": qset["id"], "confirm": True})
    assert resp.status_code == 400


# ── real questions and labelling ─────────────────────────────────────────

def log(qid, query, needs_retrieval=True, keys=(K1,)):
    usage = SimpleNamespace(input_tokens=1, output_tokens=1, llm_calls=1, cost_usd=0.0, models={"m"})
    result = {"answer": "תשובה", "route": SimpleNamespace(needs_retrieval=needs_retrieval), "full_doc_decision": None,
              "retrieval": [{"record_key": k} for k in keys], "citations": []}
    chat_log.log_question(question_id=qid, user_sub="u1", session_id="s", agent_id="test_agent", query=query,
                          filters={}, result=result, latency_ms=1, first_token_ms=None, usage=usage, ok=True)


def test_import_real_questions_then_label_them(client):
    admin_store.touch_user("u1", "someone@example.org", "Someone", None)
    log("q1", "מה מקדם הדחייה?", keys=(K2, K1, K2))
    log("q2", "שלום", needs_retrieval=False)
    log("q3", "היטל השבחה בגוש 6001")
    first = client.post("/api/admin/eval/sets/import-real", json={"agent_id": "test_agent"}).json()
    assert first["added"] == 2 and first["set"]["kind"] == "real"
    assert client.post("/api/admin/eval/sets/import-real", json={"agent_id": "test_agent"}).json()["added"] == 0

    rows = client.get(f"/api/admin/eval/sets/{first['set']['id']}/questions",
                      params={"source": "real", "unlabelled": True}).json()["questions"]
    assert len(rows) == 2 and "example.org" not in str(rows) and "u1" not in str(rows)
    q1 = next(r for r in rows if r["question"] == "מה מקדם הדחייה?")
    hints = client.get(f"/api/admin/eval/questions/{q1['id']}/suggestions").json()["decisions"]
    assert [d["record_key"] for d in hints] == [K2, K1] and hints[0]["title"] == "הכרעה 2"

    found = client.get("/api/admin/eval/lookup", params={"agent_id": "test_agent", "q": "הכרעה 3"}).json()
    assert [d["record_key"] for d in found["decisions"]] == [K3]
    by_block = client.get("/api/admin/eval/lookup", params={"agent_id": "test_agent", "q": "6002"}).json()
    assert [d["record_key"] for d in by_block["decisions"]] == [K2]
    by_key = client.get("/api/admin/eval/lookup", params={"agent_id": "test_agent", "q": K1}).json()
    assert by_key["decisions"][0]["record_key"] == K1

    assert client.patch(f"/api/admin/eval/questions/{q1['id']}", json={"gold": ["nope"]}).status_code == 400
    labelled = client.patch(f"/api/admin/eval/questions/{q1['id']}", json={"gold": [K3], "type": "principle"}).json()
    assert labelled["gold"] == [{"record_key": K3, "title": "הכרעה 3"}] and labelled["type"] == "principle"
    assert client.patch(f"/api/admin/eval/questions/{q1['id']}", json={"gold": []}).json()["gold"] == []
    assert client.delete(f"/api/admin/eval/questions/{q1['id']}").json() == {"deleted": True}
    assert client.get(f"/api/admin/eval/questions/{q1['id']}/suggestions").status_code == 404


def test_manual_question_and_set_deletion(client):
    qset = eval_store.create_set("test_agent", "ידני", "manual")
    url = f"/api/admin/eval/sets/{qset['id']}/questions"
    assert client.post(url, json={"question": "מה נקבע לגבי חניה?", "gold": [K1]}).json() == {"added": 1}
    assert client.post(url, json={"question": "מה נקבע לגבי חניה?"}).status_code == 409
    assert client.post(url, json={"question": "שאלה", "gold": ["nope"]}).status_code == 400
    overview = client.get("/api/admin/eval/overview", params={"agent_id": "test_agent"}).json()
    assert overview["sets"][0]["labelled"] == 1 and overview["runs"] == [] and "running" in overview["job"]
    assert client.delete(f"/api/admin/eval/sets/{qset['id']}").json() == {"deleted": True}
    assert client.get(f"/api/admin/eval/sets/{qset['id']}/questions").status_code == 404


def test_eval_routes_need_an_admin():
    app = FastAPI()
    app.include_router(admin_eval_api.router)
    assert TestClient(app).get("/api/admin/eval/job").status_code in (401, 403)


# ── spending ─────────────────────────────────────────────────────────────

def fake_llm_response(input_tokens=1000, output_tokens=100):
    message = SimpleNamespace(usage_metadata={"input_tokens": input_tokens, "output_tokens": output_tokens,
                                              "total_tokens": input_tokens + output_tokens},
                              response_metadata={"model_name": "gemini-2.5-flash-lite"})
    return SimpleNamespace(generations=[[SimpleNamespace(message=message)]])


def test_model_calls_in_a_pipeline_search_are_counted_across_threads(monkeypatch):
    from langchain_core.runnables.config import ensure_config
    from src.query_analyzer import QueryAnalysis
    from src.usage_tracker import UsageCollector

    def fire():  # what a LangChain model does when it finishes a call
        for cb in ensure_config().get("callbacks") or []:
            cb.on_llm_end(fake_llm_response())

    def analyze(query, history):
        fire()
        return QueryAnalysis(needs_retrieval=True, rewritten_queries=["א", "ב"], reasoning="")

    def sub_query(agent, query, filters):  # runs in a worker thread
        fire()
        return {"query": query, "top": [], "kept": [], "scores": {}, "reranked": False, "errors": {}}

    monkeypatch.setattr(eval_search.query_analyzer, "analyze_query", analyze)
    monkeypatch.setattr(eval_search, "_sub_query", sub_query)
    monkeypatch.setattr(eval_search, "metas", lambda agent, results: {})
    usage = UsageCollector()
    out = eval_search.search_one(AGENT, "שאלה", mode="pipeline", usage=usage)
    assert out["search_queries"] == ["א", "ב"] and usage.llm_calls == 3
    assert usage.cost_usd == pytest.approx(3 * (1000 * 0.10 + 100 * 0.40) / 1_000_000)
    eval_search.search_one(AGENT, "שאלה", mode="pipeline")  # no collector: nothing counted
    assert usage.llm_calls == 3


def test_budget_is_shown_editable_and_refuses_paid_work_above_it(client, monkeypatch):
    qset = labelled_set({"שאלה א": [K1]})
    monkeypatch.setattr(eval_search, "sample_chunks", lambda agent, n, seed, **kw: [dict(c) for c in CHUNKS])
    overview = client.get("/api/admin/eval/overview", params={"agent_id": "test_agent"}).json()
    assert overview["budget"] == {"budget_usd": 1.0, "spent_usd": 0.0, "remaining_usd": 1.0}

    assert client.put("/api/admin/eval/budget", json={"budget_usd": 0}).json()["remaining_usd"] == 0
    run = client.post("/api/admin/eval/runs", json={"agent_id": "test_agent", "set_id": qset["id"], "confirm": True})
    assert run.status_code == 400 and "תקציב" in run.json()["detail"]
    build = client.post("/api/admin/eval/sets/build", json={"agent_id": "test_agent", "n": 20, "confirm": True})
    assert build.status_code == 400 and "תקציב" in build.json()["detail"]
    assert eval_store.list_runs("test_agent") == [] and [s["name"] for s in eval_store.list_sets("test_agent")] == ["בדיקה"]
    assert client.put("/api/admin/eval/budget", json={"budget_usd": -1}).status_code == 422
    assert client.put("/api/admin/eval/budget", json={"budget_usd": 2.5}).json()["budget_usd"] == 2.5


def test_a_run_stops_when_its_real_cost_reaches_the_budget(client, monkeypatch):
    qset = labelled_set({f"שאלה {i}": [K1] for i in range(eval_jobs.BATCH + 5)})

    def costly(agent, query, filters=None, mode="hybrid", k=20, with_meta=True, usage=None):
        usage.on_llm_end(fake_llm_response(input_tokens=100_000))  # far above the estimate
        return {"results": ranked(K1), "search_queries": [query]}

    monkeypatch.setattr(eval_search, "search_one", costly)
    client.put("/api/admin/eval/budget", json={"budget_usd": 0.05})
    run = run_job(client, qset["id"], "יקרה", mode="pipeline")["run"]
    stopped = eval_store.get_run(run["id"])
    assert stopped["status"] == "failed" and stopped["error"] == "התקציב נגמר" and stopped["done"] == eval_jobs.BATCH
    assert stopped["cost_usd"] > 0.05 and eval_store.total_spent() == pytest.approx(stopped["cost_usd"])
    assert eval_store.budget()["remaining_usd"] == 0


def test_build_cost_is_recorded_against_the_budget(client, monkeypatch):
    monkeypatch.setattr(eval_search, "sample_chunks", lambda agent, n, seed, **kw: [dict(c) for c in CHUNKS])
    monkeypatch.setattr(eval_questions, "ask_gemini",
                        lambda prompt: ({"answerable": True, "question": "איך נקבע המחיר למטר בטבלה?"}, 0.01))
    client.post("/api/admin/eval/sets/build", json={"agent_id": "test_agent", "n": 20, "confirm": True})
    eval_jobs.wait(10)
    assert eval_store.total_spent() == pytest.approx(0.02)
    assert client.get("/api/admin/eval/overview", params={"agent_id": "test_agent"}).json()["budget"]["spent_usd"] \
        == pytest.approx(0.02)