"""Retrieval evaluation: metrics, run comparison, and the /api/worker/eval endpoints
(real LanceDB with fake embeddings; the analyzer and reranker are replaced — no Gemini)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from langchain_core.documents import Document  # noqa: E402

from backend import auth, eval_api  # noqa: E402
from src import admin_store, chat_log, eval_search, retrieval_eval as rev  # noqa: E402
from src.query_analyzer import QueryAnalysis  # noqa: E402
from tests.test_hybrid_index import AGENT as INDEX_AGENT, index  # noqa: E402,F401

KEY = "test-worker-key"


def res(*keys, sections=None, start=1):
    return [{"rank": start + i, "record_key": k, "section": (sections or {}).get(i)} for i, k in enumerate(keys)]


# ── metrics ──────────────────────────────────────────────────────────────

def test_recall_mrr_hit_and_distinct_are_counted_per_decision():
    item = {"gold": ["g"]}
    m = rev.score_item(item, {"results": res("x", "x", "y", "z", "w", "g", "g")})
    assert m["recall@5"] == 0 and m["recall@10"] == 1 and m["recall@20"] == 1
    assert m["mrr@20"] == pytest.approx(1 / 6) and m["hit@1"] == 0
    assert m["distinct@10"] == 5  # x, y, z, w, g
    top = rev.score_item(item, {"results": res("g", "x")})
    assert top["hit@1"] == 1 and top["mrr@20"] == 1
    two_gold = rev.score_item({"gold": ["a", "b"]}, {"results": res("a", "x")})
    assert two_gold["recall@5"] == 0.5


def test_results_without_a_rank_count_only_for_their_own_stage():
    results = res("x", "y") + [{"rank": None, "record_key": "g", "vector_rank": 3, "keyword_rank": None}]
    results[0]["vector_rank"], results[1]["keyword_rank"] = 1, 1
    m = rev.score_item({"gold": ["g"]}, {"results": results})
    assert m["recall@20"] == 0 and m["vector_recall@20"] == 1 and m["keyword_recall@20"] == 0


def test_section_hit_and_candidate_recall():
    item = {"gold": ["g"], "gold_section": "סעיף 4"}
    m = rev.score_item(item, {"results": res("g", sections={0: "סעיף 1"}), "candidates": res("x", "g")})
    assert m["section_hit@10"] == 0 and m["candidate_recall@20"] == 1
    m = rev.score_item(item, {"results": res("x", "g", sections={1: "סעיף 4"})})
    assert m["section_hit@10"] == 1 and "candidate_recall@20" not in m


def test_filter_accuracy_uses_result_metadata_and_date_prefixes():
    item = {"gold": [], "expected_meta": {"Committee": "ירושלים", "DecisionDate": "2023"}}
    metas = {"a": {"Committee": "ירושלים", "DecisionDate": "2023-05-01"},
             "b": {"Committee": "חיפה", "DecisionDate": "2023-01-01"},
             "c": {"Committee": " ירושלים ", "DecisionDate": "2022-12-31"}}
    m = rev.score_item(item, {"results": res("a", "b", "c", "a"), "documents": metas})
    assert m["filter_acc@10"] == 0.5 and "recall@10" not in m
    assert rev.score_item(item, {"results": res("a"), "documents": {}})["filter_acc@10"] is None
    assert rev.is_scorable(item) and not rev.is_scorable({"gold": [""], "expected_meta": {}})


def test_aggregate_per_type_only_averages_defined_metrics():
    scored = [{"type": "table", "metrics": {"recall@10": 1.0, "distinct@10": 3.0}},
              {"type": "table", "metrics": {"recall@10": 0.0, "distinct@10": 5.0}, "error": "boom"},
              {"type": "metadata", "metrics": {"filter_acc@10": 0.8, "distinct@10": 9.0}}]
    summary = rev.aggregate(scored)
    assert summary["table"] == {"n": 2, "errors": 1, "recall@10": 0.5, "distinct@10": 4.0}
    assert summary["all"]["n"] == 3 and summary["all"]["recall@10"] == 0.5
    assert summary["all"]["filter_acc@10"] == 0.8
    table = rev.summary_table(summary)
    assert "recall@10" in table and "metadata" in table


def test_compare_reports_deltas_and_flipped_questions():
    def report(values):
        items = [{"id": f"q{i}", "type": "t", "question": f"שאלה {i}", "metrics": {"recall@10": v}}
                 for i, v in enumerate(values)]
        return {"summary": rev.aggregate(items), "items": items}

    a, b = report([1.0, 0.0, 1.0]), report([0.0, 1.0, 1.0])
    b["items"].append({"id": "new", "type": "t", "question": "x", "metrics": {"recall@10": 1.0}})
    result = rev.compare(a, report([1.0, 0.0, 0.0]))
    assert result["deltas"]["all"]["recall@10"]["delta"] == pytest.approx(-1 / 3, abs=1e-3)
    assert [r["id"] for r in result["lost"]] == ["q2"] and not result["gained"]
    assert "<-- worse" in rev.delta_table(result)
    swapped = rev.compare(a, b)
    assert [r["id"] for r in swapped["gained"]] == ["q1"] and [r["id"] for r in swapped["lost"]] == ["q0"]
    assert swapped["shared"] == 3


def test_copied_questions_are_detected():
    source = "השמאי המכריע קבע כי מקדם הדחייה יעמוד על 0.9 בשל תקופת ההמתנה הארוכה למימוש"
    assert rev.longest_shared_run("מהו מקדם הדחייה שנקבע בשל המתנה ארוכה?", source) == 2
    assert rev.longest_shared_run("קבע כי מקדם הדחייה יעמוד על 0.9 בשל", source) == 8
    assert rev.longest_shared_run("", source) == 0


def test_sampling_is_one_chunk_per_decision_and_repeatable():
    keys = [(f"big_{i}", "big") for i in range(50)] + [("s1_0", "s1"), ("s2_0", "s2"), ("s3_0", "s3")]
    picks = eval_search.pick_chunks(keys, 3, seed=1)
    assert len(picks) == 3 and all(len(v) <= 3 for v in picks.values())
    assert picks == eval_search.pick_chunks(list(reversed(keys)), 3, seed=1)
    assert "big" not in eval_search.pick_chunks(keys, 10, seed=1, exclude={"big"})


# ── endpoints ────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "admin.db"
    for module in (admin_store, chat_log):
        monkeypatch.setattr(module, "_DB_PATH", db)
    admin_store._init_db()
    chat_log._init_db()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "_WORKER_API_KEY", KEY)
    monkeypatch.setattr(eval_api, "_agent", lambda agent_id: INDEX_AGENT)
    app = FastAPI()
    app.include_router(eval_api.router)
    return TestClient(app, headers={"X-Worker-Key": KEY})


def logged_questions() -> int:
    return chat_log.list_questions(days=1)["total"]


def test_eval_routes_need_the_worker_key(client):
    assert client.post("/api/worker/eval/search", json={}, headers={"X-Worker-Key": "wrong"}).status_code == 401
    assert client.get("/api/worker/eval/questions", headers={"X-Worker-Key": ""}).status_code == 401


def test_hybrid_search_reports_ranks_stages_and_metadata(client, index):
    resp = client.post("/api/worker/eval/search", json={
        "agent_id": "t", "k": 5, "mode": "hybrid",
        "items": [{"query": "פיצויים סעיף 197"}, {"query": "היטל השבחה", "filters": {"Committee": "ירושלים"}}]})
    assert resp.status_code == 200
    first, second = resp.json()["items"]
    top = first["results"][0]
    assert top["record_key"] == "b2" and top["rank"] == 1 and top["id"] == "b2_0"
    assert "fused" in top["stages"] and "keyword" in top["stages"] and top["keyword_rank"] == 1
    assert first["documents"]["b2"]["Committee"] == "ירושלים"
    assert not any(k.endswith("_int") for k in first["documents"]["b2"])
    assert {r["record_key"] for r in second["results"]} <= {"b2", "c3"}
    assert "Committee" in second["where"]
    assert logged_questions() == 0  # never written to the question log


def test_search_rejects_too_many_items_and_bad_mode(client):
    many = [{"query": f"שאלה {i}"} for i in range(eval_api.MAX_ITEMS + 1)]
    assert client.post("/api/worker/eval/search", json={"agent_id": "t", "items": many}).status_code == 422
    one = [{"query": "x"}]
    assert client.post("/api/worker/eval/search",
                       json={"agent_id": "t", "items": one, "mode": "answer"}).status_code == 422
    assert client.post("/api/worker/eval/search",
                       json={"agent_id": "t", "items": one, "k": 500}).status_code == 422


def test_pipeline_runs_analysis_search_and_rerank_per_search_query(client, index, monkeypatch):
    seen = []

    def analyze(query, history, agent=None):
        if "שלום" in query:
            return QueryAnalysis(needs_retrieval=False, rewritten_queries=[], reasoning="greeting")
        return QueryAnalysis(needs_retrieval=True, rewritten_queries=["פיצויים סעיף 197", "היטל השבחה"],
                             reasoning="two topics")

    def rerank(query, docs: list[Document], top_k=10):
        seen.append(query)
        for i, d in enumerate(docs):
            d.metadata["_rerank"] = i
        return list(reversed(docs))[:1]  # keep only the weakest-looking chunk, to see the rerank applied

    monkeypatch.setattr(eval_search.query_analyzer, "analyze_query", analyze)
    monkeypatch.setattr(eval_search.reranker, "rerank_documents", rerank)
    resp = client.post("/api/worker/eval/search", json={
        "agent_id": "t", "mode": "pipeline", "items": [{"query": "פיצויים והיטל"}, {"query": "שלום"}]})
    assert resp.status_code == 200
    out, chitchat = resp.json()["items"]
    assert out["search_queries"] == ["פיצויים סעיף 197", "היטל השבחה"] and sorted(seen) == sorted(out["search_queries"])
    assert len(out["results"]) == 2 and [r["rank"] for r in out["results"]] == [1, 2]
    assert all("reranked" in r["stages"] for r in out["results"]) and out["reranked"]
    assert [r["sub_query"] for r in out["results"]] == [0, 1]
    assert len(out["candidates"]) > len(out["results"]) and out["candidates"][0]["record_key"] == "b2"
    assert any("reranked" not in c["stages"] for c in out["candidates"])
    assert chitchat["route"] == "chitchat" and chitchat["results"] == []
    assert logged_questions() == 0


def test_pipeline_falls_back_to_the_raw_question_when_analysis_fails(client, index, monkeypatch):
    def broken(query, history, agent=None):
        raise RuntimeError("402 prepaid credit")

    monkeypatch.setattr(eval_search.query_analyzer, "analyze_query", broken)
    monkeypatch.setattr(eval_search.reranker, "rerank_documents", lambda q, docs, top_k=10: docs[:top_k])
    out = client.post("/api/worker/eval/search", json={
        "agent_id": "t", "mode": "pipeline", "items": [{"query": "פיצויים סעיף 197"}]}).json()["items"][0]
    assert out["search_queries"] == ["פיצויים סעיף 197"] and out["error"].startswith("analyzer")
    assert out["results"][0]["record_key"] == "b2" and out["reranked"] is False


def test_sample_returns_one_chunk_per_decision_with_metadata(client, index):
    resp = client.post("/api/worker/eval/sample", json={"agent_id": "t", "n": 10, "seed": 3, "min_chars": 0})
    chunks = resp.json()["chunks"]
    assert sorted(c["record_key"] for c in chunks) == ["a1", "b2", "c3"]
    a1 = next(c for c in chunks if c["record_key"] == "a1")
    assert a1["text"] and a1["section"] and a1["meta"]["Block"] == "6426"
    assert "PublicityDate_int" not in a1["meta"]
    again = client.post("/api/worker/eval/sample", json={"agent_id": "t", "n": 10, "seed": 3, "min_chars": 0})
    assert again.json() == resp.json()
    only = client.post("/api/worker/eval/sample", json={"agent_id": "t", "n": 5, "exclude": ["a1", "b2"]})
    assert [c["record_key"] for c in only.json()["chunks"]] == ["c3"]
    assert client.post("/api/worker/eval/sample", json={"agent_id": "t", "n": 500}).status_code == 422


def test_real_questions_are_text_and_time_only(client):
    from types import SimpleNamespace

    admin_store.touch_user("u1", "someone@example.org", "Someone", None)
    usage = SimpleNamespace(input_tokens=1, output_tokens=1, llm_calls=1, cost_usd=0.0, models={"m"})

    def log(qid, query, needs_retrieval=True):
        result = {"answer": "תשובה", "route": SimpleNamespace(needs_retrieval=needs_retrieval),
                  "full_doc_decision": None, "retrieval": [{"record_key": "k"}], "citations": []}
        chat_log.log_question(question_id=qid, user_sub="u1", session_id="s", agent_id="t", query=query,
                              filters={}, result=result, latency_ms=1, first_token_ms=None, usage=usage, ok=True)

    log("q1", "מה מקדם הדחייה?")
    log("q2", "מה  מקדם הדחייה? ")
    log("q3", "שלום", needs_retrieval=False)
    log("q4", "היטל השבחה בגוש 6426")
    body = client.get("/api/worker/eval/questions", params={"days": 30}).json()
    assert sorted(q["question"] for q in body["questions"]) == ["היטל השבחה בגוש 6426", "מה מקדם הדחייה?"]
    assert all(set(q) == {"ts", "question"} for q in body["questions"])
    assert "example.org" not in str(body) and "u1" not in str(body)
    assert len(client.get("/api/worker/eval/questions", params={"limit": 1}).json()["questions"]) == 1
