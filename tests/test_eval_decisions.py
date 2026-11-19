"""Benchmark questions written from whole decisions (src/eval_decisions.py): naming a
decision, checking the model's quotes, topic gold sets, the build job and storing the
answer. The model, the index and the library are replaced — no network, no Gemini.
All decisions here are fictional."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import admin_eval_api, auth  # noqa: E402
from src import admin_store, eval_decisions as ed, eval_jobs, eval_search, eval_store  # noqa: E402
from src import retrieval_eval as rev  # noqa: E402

AGENT = {"id": "test_agent", "name": "בדיקה", "filters": []}
TITLE = "הכרעת שמאי מכריע מיום 04-05-2025 בעניין היטל השבחה כהן משה נ ועדה מקומית חיפה"
TEXT = ("רקע. המבקש הגיש בקשה להיתר בנייה. עילת המימוש היא קבלת היתר בנייה מספר 2023-118 מיום 1.2.2023. "
        "המועד הקובע לצורך השומה הוא 1.2.2023. | שווי | 12,500 ₪ |\n"
        "שמאי הוועדה טען כי שווי מ״ר מבונה הוא 14,000 ₪ ואילו שמאי המבקש טען ל-11,000 ₪. ") * 20


def doc(key="k1", title=TITLE, meta=None, text=TEXT):
    return {"record_key": key, "title": title, "meta": meta or {"AppraisalType": "היטל השבחה",
                                                                 "Block": "1234", "Plot": "56"}, "text": text}


# ── naming a decision ────────────────────────────────────────────────────

def test_parties_and_references_come_from_the_title():
    p = ed.parties(TITLE, "היטל השבחה")
    assert p == {"date": "04-05-2025", "applicant": "כהן משה", "respondent": "ועדה מקומית חיפה"}
    assert ed.parties("כותרת בלי מבנה", None) is None
    refs = ed.references(doc())
    assert "כהן משה נ' ועדה מקומית חיפה" in refs and "גוש 1234 חלקה 56" in refs
    item = ed.named_question(doc(), 0)
    assert item["type"] == "named" and item["gold"] == ["k1"] and "כהן משה" in item["question"]


# ── checking the model's answers ─────────────────────────────────────────

def test_a_fact_needs_a_verbatim_quote_and_a_question_in_its_own_words():
    good = {"field": "מועד קובע", "question": "מה המועד הקובע בתיק כהן נ' ועדה מקומית חיפה?",
            "answer": "1.2.2023", "quote": "המועד הקובע לצורך השומה הוא 1.2.2023."}
    invented = {**good, "question": "מה מספר ההיתר בתיק כהן?", "quote": "ההיתר ניתן ביום 5.5.2020 לבניית שתי קומות"}
    copied = {**good, "question": "המועד הקובע לצורך השומה הוא 1.2.2023 נכון בתיק כהן?"}
    quoted_marks = {**good, "question": "כמה טען שמאי הוועדה לשווי מ\"ר בתיק כהן?",
                    "answer": "14,000 ₪", "quote": 'שמאי הוועדה טען כי שווי מ"ר מבונה הוא 14,000 ₪'}
    answer = {"facts": [good, invented, copied, quoted_marks],
              "analysis": {"question": "על מה נחלקו שמאי הצדדים בתיק כהן?", "points": ["שווי מ\"ר מבונה", ""]}}
    items = ed.doc_items(doc(), answer, k=4)
    facts = [i for i in items if i["type"] == "fact"]
    assert [f["answer"]["value"] for f in facts] == ["1.2.2023", "14,000 ₪"]   # ״ and " match
    analysis = [i for i in items if i["type"] == "analysis"]
    assert analysis[0]["answer"] == {"points": ['שווי מ"ר מבונה']} and analysis[0]["gold"] == ["k1"]
    assert ed.doc_items(doc(), {"facts": "garbage", "analysis": None}) == []


# ── topics ───────────────────────────────────────────────────────────────

def rows_for(n_by_committee: dict) -> list[dict]:
    rows, i = [], 0
    for committee, n in n_by_committee.items():
        for _ in range(n):
            rows.append({"record_key": f"d{i}", "text": f"פתיח. נדון כופר חניה בסך 50,000 ₪ {i}",
                         "meta": f'{{"Committee": "{committee}"}}'})
            rows.append({"record_key": f"d{i}", "text": "קטע שני של אותה החלטה, כופר חניה", "meta": "{}"})
            i += 1
    rows.append({"record_key": "other", "text": "לא קשור", "meta": "{}"})
    return rows


def test_a_topic_is_narrowed_to_a_committee_when_too_many_decisions_mention_it():
    topic = ed.TOPICS[0]
    few = ed.topic_candidates(rows_for({"ועדה מקומית חיפה": 4}), topic)
    assert len(few) == 4 and "other" not in few
    assert ed.topic_scopes(few) == [("", sorted(few))]
    many = ed.topic_candidates(rows_for({"ועדה מקומית חיפה": 5, "ועדה מקומית אילת": 30, "רעננה": 1}), topic)
    scopes = ed.topic_scopes(many)
    assert [s for s, _ in scopes] == ["ועדה מקומית חיפה"]           # 30 is too many, 1 too few
    assert ed.scope_text("ועדה מקומית חיפה") == " בועדה מקומית חיפה"
    item = ed.topic_item(topic, scopes[0][0], scopes[0][1], {"relevant": [1, 3, "x"]})
    assert item["gold"] == [scopes[0][1][0], scopes[0][1][2]] and item["type"] == "topic"
    assert item["expected_meta"] == {"Committee": "ועדה מקומית חיפה"}
    assert ed.topic_item(topic, "", ["a"], {"relevant": []}) is None


def test_precision_counts_distinct_decisions_in_the_top_ten():
    assert rev.precision_at(["a", "a", "b", "c"], {"a", "c"}) == pytest.approx(2 / 3)
    assert rev.precision_at([], {"a"}) is None
    item = {"gold": ["a", "c"]}
    outcome = {"results": [{"rank": 1, "record_key": "a"}, {"rank": 2, "record_key": "b"}]}
    assert rev.score_item(item, outcome)["precision@10"] == 0.5
    assert "precision@10" not in rev.score_item({"gold": ["a"]}, outcome)


# ── a whole set ──────────────────────────────────────────────────────────

def docs(n: int) -> list[dict]:
    return [doc(f"k{i}", title=TITLE.replace("כהן משה", f"לוי {i}"),
                meta={"AppraisalType": "היטל השבחה", **({"Block": str(100 + i), "Plot": "7"} if i % 2 else {})})
            for i in range(n)]


def fake_ask(prompt: str):
    if "הנושא:" in prompt:
        return {"relevant": [1, 2]}, 0.001
    return {"facts": [{"field": "מועד קובע", "question": "מה המועד הקובע בתיק הזה של לוי?", "answer": "1.2.2023",
                       "quote": "המועד הקובע לצורך השומה הוא 1.2.2023."},
                      {"field": "עילה", "question": "מהי עילת המימוש אצל לוי?", "answer": "היתר",
                       "quote": "עילת המימוש היא קבלת היתר בנייה מספר 2023-118"}],
            "analysis": {"question": "על מה נחלקו הצדדים בתיק של לוי?", "points": ["שווי מ\"ר"]}}, 0.002


def test_plan_and_write_make_every_type_and_respect_the_counts():
    planned = ed.plan(docs(40), 20, seed=1)
    want = planned["want"]
    assert sum(want.values()) == 20
    kinds = [i["type"] for i in planned["free_items"]]
    assert kinds.count("named") == want["named"] and kinds.count("block_plot") == want["block_plot"]
    assert planned["cost_usd"] > 0 and planned["n_docs"] >= want["analysis"]
    seen = []
    written = ed.write(planned, topic_rows=lambda t: rows_for({"ועדה מקומית חיפה": 3}), ask=fake_ask,
                       on_item=lambda item, spent: seen.append(item))
    types = [i["type"] for i in written["items"]]
    assert types.count("fact") == want["fact"] and types.count("analysis") == want["analysis"]
    assert types.count("topic") == 1      # only the parking-levy patterns match these rows
    assert seen == written["items"] and written["spent"] > 0 and written["stopped"] is None


def test_a_billing_error_stops_the_writing():
    planned = ed.plan(docs(20), 20, seed=1)

    def broke(prompt):
        raise RuntimeError("402 RESOURCE_EXHAUSTED prepayment")
    written = ed.write(planned, topic_rows=lambda t: [], ask=broke)
    assert written["items"] == [] and "402" in written["stopped"]


# ── the job, the route and the stored answer ─────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "admin.db"
    for module in (admin_store, eval_store):
        monkeypatch.setattr(module, "_DB_PATH", db)
    admin_store._init_db()
    eval_store._init_db()
    monkeypatch.setattr(admin_eval_api, "_agent", lambda agent_id: AGENT)
    monkeypatch.setattr(eval_search, "sample_decisions", lambda agent, n, seed: docs(n))
    monkeypatch.setattr(eval_search, "topic_rows", lambda agent, patterns: rows_for({"ועדה מקומית חיפה": 3}))
    monkeypatch.setattr(ed.eq, "ask_gemini", lambda prompt, max_tokens=400: fake_ask(prompt))
    monkeypatch.setattr(admin_eval_api, "_titles", lambda agent_id, keys: {})
    app = FastAPI()
    app.include_router(admin_eval_api.router)
    app.dependency_overrides[auth.require_admin] = lambda: {"email": "admin@example.org"}
    yield TestClient(app)
    eval_jobs.wait(10)


def test_the_decisions_build_stores_answers_and_a_person_can_check_them(client):
    body = {"agent_id": "test_agent", "n": 20, "mode": "decisions"}
    estimate = client.post("/api/admin/eval/sets/build", json=body).json()["estimate"]
    assert estimate["mode"] == "decisions" and estimate["cost_usd"] > 0
    started = client.post("/api/admin/eval/sets/build", json={**body, "confirm": True})
    assert started.status_code == 200, started.text
    eval_jobs.wait(10)
    set_id = started.json()["set"]["id"]
    assert eval_store.get_set(set_id)["status"] == "ready"
    page = client.get(f"/api/admin/eval/sets/{set_id}/questions", params={"type": "fact"}).json()
    fact = page["questions"][0]
    assert fact["answer"]["value"] and fact["answer"]["quote"] and fact["verified"] is None
    assert set(page["facets"]["types"]) >= {"named", "fact", "analysis", "topic"}
    marked = client.patch(f"/api/admin/eval/questions/{fact['id']}", json={"verified": "wrong"}).json()
    assert marked["verified"] is False and marked["answer"] == fact["answer"]
    cleared = client.patch(f"/api/admin/eval/questions/{fact['id']}", json={"verified": "unchecked"}).json()
    assert cleared["verified"] is None
    assert client.post("/api/admin/eval/sets/build", json={**body, "mode": "pages"}).status_code == 422
