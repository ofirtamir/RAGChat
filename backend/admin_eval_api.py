"""Admin console: retrieval benchmark (/api/admin/eval/*).

Question sets, their questions and gold decisions, measurement runs and their
comparison — all kept in the server's database (src/eval_store.py). Building a
set and running one are background jobs on the server (src/eval_jobs.py), one at
a time. Anything that spends money (building a set with the model, any run) first
answers with its estimate and needs a second call with confirm=true; it is refused
when the estimate is above what is left of the benchmark budget (settings:
eval_budget_usd), and the job stops itself if the real cost reaches it.

Same access rule as backend/admin_api: a verified Google token whose email is in ADMIN_EMAILS.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src import admin_store, chat_log, eval_jobs, eval_search, eval_store, library_store, retrieval_eval as rev
from .admin_api import _agent
from .auth import require_admin

router = APIRouter(prefix="/api/admin/eval", dependencies=[Depends(require_admin)])

MIN_BUILD, MAX_BUILD = 20, 150
MAX_FLIPS = 200


def _set(set_id: str) -> dict:
    qset = eval_store.get_set(set_id)
    if qset is None:
        raise HTTPException(status_code=404, detail="הסט לא נמצא")
    return qset


def _question(question_id: str) -> dict:
    question = eval_store.get_question(question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="השאלה לא נמצאה")
    return question


def _busy(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _titles(agent_id: str, keys) -> dict[str, str]:
    return library_store.titles(agent_id, list(keys))


def _with_titles(agent_id: str, questions: list[dict]) -> list[dict]:
    """Gold and retrieved decisions as {record_key, title}."""
    keys = {k for q in questions for k in q["gold"]}
    keys |= {t["record_key"] for q in questions if q.get("result") for t in q["result"]["top"]}
    titles = _titles(agent_id, keys)
    for q in questions:
        q["gold"] = [{"record_key": k, "title": titles.get(k)} for k in q["gold"]]
        for t in (q.get("result") or {}).get("top", []):
            t["title"] = titles.get(t["record_key"])
    return questions


# ── overview ─────────────────────────────────────────────────────────────

@router.get("/overview")
def overview(agent_id: str):
    """Everything the tab needs on load: sets, runs (newest first) and the job."""
    _agent(agent_id)
    return {"sets": eval_store.list_sets(agent_id), "runs": eval_store.list_runs(agent_id),
            "job": eval_jobs.status(), "budget": eval_store.budget()}


class BudgetRequest(BaseModel):
    budget_usd: float = Field(ge=0, le=100)


@router.put("/budget")
def set_budget(req: BudgetRequest):
    """The total the benchmark may spend (building sets and runs, all time)."""
    admin_store.update_settings({"eval_budget_usd": req.budget_usd})
    return eval_store.budget()


@router.get("/job")
def job():
    return eval_jobs.status()


@router.post("/job/cancel")
def job_cancel():
    return {"cancelled": eval_jobs.cancel()}


# ── sets ─────────────────────────────────────────────────────────────────

class BuildRequest(BaseModel):
    agent_id: str
    name: str = Field(default="", max_length=80)
    n: int = Field(default=60, ge=MIN_BUILD, le=MAX_BUILD)
    seed: int = Field(default=7, ge=0, le=1_000_000)
    meta_share: float = Field(default=0.4, ge=0, le=1)
    mode: Literal["decisions", "chunks"] = "chunks"
    confirm: bool = False


@router.post("/sets/build")
def build_set(req: BuildRequest):
    """Without confirm: the estimate (reads the index, spends nothing). With
    confirm: start writing the set in the background (paid)."""
    agent = _agent(req.agent_id)
    try:
        estimate = eval_jobs.estimate_build(agent, req.n, req.seed, req.meta_share, req.mode)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"דגימת האינדקס נכשלה: {str(exc)[:200]}")
    if not estimate["sampled"]:
        raise HTTPException(status_code=400, detail="האינדקס ריק — אין קטעים לכתוב מהם שאלות")
    if not req.confirm:
        return {"estimate": estimate, "budget": eval_store.budget()}
    name = req.name.strip() or (f"שאלות בסגנון שמאים, {req.n}" if req.mode == "decisions"
                                else f"סינתטי {estimate['sampled']} החלטות")
    try:
        qset = eval_jobs.start_build(agent, name, req.n, req.seed, req.meta_share, estimate["cost_usd"], req.mode)
    except eval_jobs.JobBusy as exc:
        raise _busy(exc)
    except eval_jobs.BudgetExceeded as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"estimate": estimate, "set": qset, "job": eval_jobs.status(), "budget": eval_store.budget()}


class ImportRequest(BaseModel):
    agent_id: str
    days: int = Field(default=90, ge=1, le=366)
    limit: int = Field(default=500, ge=1, le=2000)


@router.post("/sets/import-real")
def import_real(req: ImportRequest):
    """Copy recent real questions (text and time only) into the agent's real-questions
    set. They count in runs once a gold decision is marked."""
    _agent(req.agent_id)
    qset = eval_store.real_set(req.agent_id)
    fetched = eval_search.real_questions(req.days, req.limit, with_id=True)
    items = [{"question": q["question"], "type": "real", "asked_at": q["ts"], "log_id": q["log_id"]}
             for q in fetched]
    added = eval_store.add_questions(qset["id"], items, "real")
    return {"added": added, "found": len(fetched), "set": eval_store.get_set(qset["id"])}


class NewQuestion(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    type: str = Field(default="manual", max_length=40)
    gold: list[str] = Field(default_factory=list, max_length=20)
    gold_section: str | None = Field(default=None, max_length=200)


def _check_gold(agent_id: str, gold: list[str]) -> list[str]:
    gold = list(dict.fromkeys(g.strip() for g in gold if g and g.strip()))
    unknown = set(gold) - library_store.known_keys(agent_id, gold)
    if unknown:
        raise HTTPException(status_code=400, detail=f"החלטה לא מוכרת בספרייה: {', '.join(sorted(unknown))[:200]}")
    return gold


@router.post("/sets/{set_id}/questions")
def add_question(set_id: str, req: NewQuestion):
    qset = _set(set_id)
    gold = _check_gold(qset["agent_id"], req.gold)
    added = eval_store.add_questions(set_id, [{"question": req.question, "type": req.type.strip() or "manual",
                                               "gold": gold, "gold_section": req.gold_section}], "manual")
    if not added:
        raise HTTPException(status_code=409, detail="השאלה כבר קיימת בסט")
    return {"added": added}


@router.delete("/sets/{set_id}")
def delete_set(set_id: str):
    _set(set_id)
    state = eval_jobs.status()
    if state["running"] and state["set_id"] == set_id:
        raise HTTPException(status_code=409, detail="עבודה רצה על הסט הזה")
    return {"deleted": eval_store.delete_set(set_id)}


@router.get("/sets/{set_id}/questions")
def questions(set_id: str, type: str | None = None, source: str | None = None, q: str | None = None,
              run_id: str | None = None, missed: bool = False, unlabelled: bool = False, page: int = 1):
    qset = _set(set_id)
    if source and source not in eval_store.SOURCES:
        raise HTTPException(status_code=400, detail="מקור לא תקין")
    data = eval_store.list_questions(set_id, qtype=type, source=source, q=q, run_id=run_id, missed=missed,
                                     unlabelled=unlabelled, page=page)
    data["questions"] = _with_titles(qset["agent_id"], data["questions"])
    data["facets"] = eval_store.facets(set_id)
    return data


# ── questions ────────────────────────────────────────────────────────────

class QuestionPatch(BaseModel):
    gold: list[str] | None = Field(default=None, max_length=20)
    gold_section: str | None = Field(default=None, max_length=200)
    type: str | None = Field(default=None, max_length=40)
    verified: Literal["correct", "wrong", "unchecked"] | None = None


@router.patch("/questions/{question_id}")
def patch_question(question_id: str, patch: QuestionPatch):
    """Set or clear (gold=[]) the gold decision(s), the gold section or the type, or
    record a person's check of the stored answer."""
    question = _question(question_id)
    agent_id = _set(question["set_id"])["agent_id"]
    gold = _check_gold(agent_id, patch.gold) if patch.gold is not None else None
    updated = eval_store.update_question(
        question_id, gold=gold, gold_section=patch.gold_section, qtype=patch.type,
        verified={"correct": True, "wrong": False}.get(patch.verified or ""),
        clear_verified=patch.verified == "unchecked")
    return _with_titles(agent_id, [{**updated, "result": None}])[0]


@router.delete("/questions/{question_id}")
def delete_question(question_id: str):
    _question(question_id)
    return {"deleted": eval_store.delete_question(question_id)}


@router.get("/questions/{question_id}/suggestions")
def suggestions(question_id: str):
    """For an imported real question: the decisions the chat's answer drew on."""
    question = _question(question_id)
    agent_id = _set(question["set_id"])["agent_id"]
    logged = chat_log.get_question(question["log_id"]) if question.get("log_id") else None
    keys = list(dict.fromkeys(r.get("record_key") for r in (logged or {}).get("retrieval") or []
                              if r.get("record_key")))
    titles = _titles(agent_id, keys)
    return {"decisions": [{"record_key": k, "title": titles.get(k)} for k in keys if k in titles]}


@router.get("/lookup")
def lookup(agent_id: str, q: str):
    """Find a decision to mark as gold: by title, by exact record_key, or by block number."""
    agent = _agent(agent_id)
    q = q.strip()[:200]
    if not q:
        return {"decisions": []}
    found = library_store.search(agent, q=q, page_size=10)["documents"]
    if q.isdigit() and any(f["name"] == "Block" for f in agent.get("filters", [])):
        found += library_store.search(agent, filters={"Block": q}, page_size=10)["documents"]
    exact = library_store.get_document(agent_id, q, with_parsed=False)
    if exact:
        found.insert(0, exact)
    seen, out = set(), []
    for d in found:
        if d["record_key"] not in seen:
            seen.add(d["record_key"])
            out.append({"record_key": d["record_key"], "title": d["title"], "sort_date": d.get("sort_date"),
                        "status": d.get("status")})
    return {"decisions": out[:15]}


# ── runs ─────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    agent_id: str
    set_id: str
    mode: Literal["hybrid", "pipeline"] = "hybrid"
    label: str = Field(default="", max_length=60)
    k: int = Field(default=20, ge=10, le=50)
    confirm: bool = False


@router.post("/runs")
def start_run(req: RunRequest):
    """Without confirm: the estimated cost (hybrid = question embeddings only, ~free;
    pipeline = analysis and rerank calls too). With confirm: start the run, unless
    the estimate is above what is left of the benchmark budget."""
    agent = _agent(req.agent_id)
    qset = _set(req.set_id)
    if qset["agent_id"] != agent["id"]:
        raise HTTPException(status_code=400, detail="הסט שייך לספרייה אחרת")
    estimate = eval_jobs.run_estimate(req.set_id, req.mode)
    if not estimate["questions"]:
        raise HTTPException(status_code=400, detail="אין בסט שאלות עם החלטה נכונה מסומנת")
    if not req.confirm:
        return {"estimate": estimate, "budget": eval_store.budget()}
    try:
        run = eval_jobs.start_run(agent, req.set_id, req.mode, req.label, req.k)
    except eval_jobs.JobBusy as exc:
        raise _busy(exc)
    except ValueError as exc:  # BudgetExceeded too
        raise HTTPException(status_code=400, detail=str(exc))
    return {"estimate": estimate, "run": run, "job": eval_jobs.status(), "budget": eval_store.budget()}


@router.get("/runs")
def runs(agent_id: str):
    _agent(agent_id)
    return {"runs": eval_store.list_runs(agent_id)}


@router.get("/runs/{run_id}")
def run(run_id: str):
    found = eval_store.get_run(run_id)
    if found is None:
        raise HTTPException(status_code=404, detail="הריצה לא נמצאה")
    return found


@router.get("/compare")
def compare(a: str, b: str, metric: str = "recall@10"):
    """Run B against run A: delta per metric and question type, and the questions
    that got better or worse on `metric` (matched by question, so same set)."""
    if metric not in rev.METRICS:
        raise HTTPException(status_code=400, detail="מדד לא מוכר")
    ra, rb = eval_store.run_report(a), eval_store.run_report(b)
    if ra is None or rb is None:
        raise HTTPException(status_code=404, detail="הריצה לא נמצאה")
    result = rev.compare(ra, rb, flip_metric=metric)
    ranks_a = {i["id"]: i["found_rank"] for i in ra["items"]}
    ranks_b = {i["id"]: i["found_rank"] for i in rb["items"]}
    for row in result["gained"] + result["lost"]:
        row["rank_a"], row["rank_b"] = ranks_a.get(row["id"]), ranks_b.get(row["id"])
    strip = ("items", "summary")
    return {**result, "gained": result["gained"][:MAX_FLIPS], "lost": result["lost"][:MAX_FLIPS],
            "gained_total": len(result["gained"]), "lost_total": len(result["lost"]),
            "same_set": ra["set_id"] == rb["set_id"],
            "a": {k: v for k, v in ra.items() if k not in strip}, "b": {k: v for k, v in rb.items() if k not in strip}}
