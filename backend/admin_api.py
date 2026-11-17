"""Admin console API (/api/admin/*) and the local indexing worker API (/api/worker/*).

Every /api/admin route requires a verified Google token whose email is listed
in ADMIN_EMAILS. The worker routes use a shared secret (WORKER_API_KEY) because
the worker is a script on the indexing machine, not a signed-in browser.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src import admin_store, library_batch, library_index, library_store, library_sync
from .auth import require_admin, require_worker

admin = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])
worker = APIRouter(prefix="/api/worker", dependencies=[Depends(require_worker)])


def _agent(agent_id: str) -> dict:
    from src.agents import get_agent

    agent = get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {agent_id}")
    return agent


# ── overview / usage / settings ──────────────────────────────────────────

@admin.get("/overview")
def overview():
    from src.agents import list_agents

    users = admin_store.list_users()
    by_status = {s: 0 for s in admin_store.USER_STATUSES}
    for u in users:
        by_status[u["status"]] += 1
    usage = admin_store.usage_summary(days=14)
    return {
        "users": {"total": len(users), **by_status,
                  "active_today": sum(1 for u in users if u["questions_today"])},
        "usage": usage,
        "settings": admin_store.get_settings(),
        "libraries": [
            {"agent_id": a["id"], "name": a["name"], **library_store.status_counts(a["id"])}
            for a in list_agents()
        ],
        "worker": library_store.worker_status(),
    }


@admin.get("/usage")
def usage(days: int = 30):
    summary = admin_store.usage_summary(days=days)
    users = sorted(admin_store.list_users(), key=lambda u: -u["cost_month"])
    summary["top_users"] = [
        {k: u[k] for k in ("sub", "email", "name", "questions_today", "questions_month", "cost_month")}
        for u in users[:20] if u["questions_month"]
    ]
    return summary


@admin.get("/settings")
def get_settings():
    return admin_store.get_settings()


@admin.put("/settings")
def put_settings(changes: dict):
    try:
        return admin_store.update_settings(changes)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="ערך לא תקין")


# ── users ────────────────────────────────────────────────────────────────

class UserPatch(BaseModel):
    status: str | None = None
    daily_limit: int | None = Field(default=None, ge=0)
    clear_daily_limit: bool = False
    note: str | None = None


@admin.get("/users")
def users():
    return {"users": admin_store.list_users(), "settings": admin_store.get_settings()}


@admin.patch("/users/{sub}")
def patch_user(sub: str, patch: UserPatch):
    if admin_store.get_user(sub) is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        return admin_store.update_user(sub, status=patch.status, daily_limit=patch.daily_limit,
                                       clear_daily_limit=patch.clear_daily_limit, note=patch.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── document library ─────────────────────────────────────────────────────

class LibraryQuery(BaseModel):
    agent_id: str
    status: str | None = None
    q: str | None = None
    filters: dict | None = None
    page: int = 1
    page_size: int = 50


class LibraryAction(BaseModel):
    agent_id: str
    action: str  # queue | queue_and_index | cancel | index | unindex | retry
    keys: list[str] | None = None
    selection: dict | None = None  # {status, q, filters, limit} = "everything matching"


@admin.get("/library/agents")
def library_agents():
    from src.agents import list_agents

    return {"agents": [
        {"id": a["id"], "name": a["name"], "filters": a.get("filters", []),
         **library_store.status_counts(a["id"])}
        for a in list_agents()
    ], "worker": library_store.worker_status(), "sync": library_sync.get_status(),
       "batches": library_batch.status()}


@admin.post("/library/search")
def library_search(query: LibraryQuery):
    agent = _agent(query.agent_id)
    try:
        result = library_store.search(agent, status=query.status, q=query.q, filters=query.filters,
                                      page=query.page, page_size=query.page_size)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    result.update(library_store.status_counts(agent["id"]))
    result["worker"] = library_store.worker_status()
    return result


@admin.get("/library/{agent_id}/{key}")
def library_document(agent_id: str, key: str):
    doc = library_store.get_document(agent_id, key)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@admin.post("/library/action")
def library_action(req: LibraryAction):
    agent = _agent(req.agent_id)
    if not req.keys and req.selection is None:
        raise HTTPException(status_code=400, detail="לא נבחרו מסמכים")
    move = lambda to, frm, **kw: library_store.transition(  # noqa: E731
        agent, to, frm, keys=req.keys, selection=req.selection, **kw)

    if req.action in ("queue", "queue_and_index"):
        moved = move("queued", ("catalog", "failed", "no_text", "parsed"),
                     auto_index=req.action == "queue_and_index")
    elif req.action == "cancel":
        moved = move("catalog", ("queued", "processing"))
    elif req.action == "index":
        moved = move("indexing", ("parsed", "indexed"))
        library_index.enqueue(agent["id"], moved)
    elif req.action == "unindex":
        moved = move("parsed", ("indexed",))
        for key in moved:
            library_index.remove_from_index(agent["id"], key)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")
    return {"moved": len(moved), **library_store.status_counts(agent["id"])}


@admin.post("/library/{agent_id}/sync")
def library_sync_start(agent_id: str):
    result = library_sync.start(_agent(agent_id))
    if "error" in result:
        raise HTTPException(status_code=409, detail=result["error"])
    return result


# ── worker (runs on the indexing machine) ────────────────────────────────

class CatalogBatch(BaseModel):
    agent_id: str
    records: list[dict] = Field(max_length=2000)


class ClaimRequest(BaseModel):
    agent_id: str
    limit: int = 5


class ParseResult(BaseModel):
    agent_id: str
    record_key: str
    ok: bool = True
    pages: list[dict] = []        # [{number, markdown}]
    tables: int = 0
    problems: list[dict] = []     # [{page, problems: [...]}] for tables that failed validation
    llm_tables: int = Field(default=0, ge=0)          # tables re-read by Gemini on the indexing machine
    parse_cost_usd: float = Field(default=0.0, ge=0)  # what those re-reads cost
    error: str | None = None


MIN_TEXT_CHARS = 100  # less than this across the whole PDF means a scan with no text layer


@worker.post("/catalog")
def worker_catalog(batch: CatalogBatch):
    return library_store.upsert_catalog(_agent(batch.agent_id), batch.records)


@worker.post("/claim")
def worker_claim(req: ClaimRequest):
    _agent(req.agent_id)
    library_store.heartbeat()
    return {"documents": library_store.claim(req.agent_id, req.limit)}


class RequeueRequest(BaseModel):
    agent_id: str
    keys: list[str] = Field(min_length=1, max_length=2000)


@worker.post("/requeue")
def worker_requeue(req: RequeueRequest):
    """Parse these documents again (their pictures were read through the Batch API
    and are now cached on the indexing machine). An indexed document keeps serving
    its old chunks until the new parse is indexed in their place."""
    agent = _agent(req.agent_id)
    indexed = library_store.transition(agent, "queued", ("indexed",), keys=req.keys, auto_index=True)
    others = library_store.transition(agent, "queued", ("parsed", "failed", "no_text"), keys=req.keys)
    return {"queued": len(indexed) + len(others)}


@worker.post("/result")
def worker_result(result: ParseResult):
    library_store.heartbeat()
    doc = library_store.get_document(result.agent_id, result.record_key, with_parsed=False)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if not result.ok:
        library_store.set_status(result.agent_id, result.record_key, "failed",
                                 error=(result.error or "הפרסור נכשל")[:400])
        return {"status": "failed"}
    if sum(len(p.get("markdown", "")) for p in result.pages) < MIN_TEXT_CHARS:
        library_store.set_status(result.agent_id, result.record_key, "no_text",
                                 error="אין שכבת טקסט בקובץ (כנראה סריקה)")
        return {"status": "no_text"}
    library_store.save_parsed(result.agent_id, result.record_key, result.pages,
                              result.tables, result.problems, llm_tables=result.llm_tables,
                              parse_cost_usd=result.parse_cost_usd)
    if result.parse_cost_usd:
        admin_store.record_usage(user_sub=None, kind="parsing", model="table-fallback",
                                 input_tokens=0, output_tokens=0, agent_id=result.agent_id,
                                 cost_usd=result.parse_cost_usd)
    if doc["auto_index"]:
        library_store.set_status(result.agent_id, result.record_key, "indexing")
        library_index.enqueue(result.agent_id, [result.record_key])
        return {"status": "indexing"}
    return {"status": "parsed"}
