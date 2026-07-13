"""Agent registry: each agent is a self-contained knowledge base definition.

An agent = one data source (e.g. a gov.il dynamic collector) with:
- its own Chroma collection (isolated from other agents and from manual uploads)
- a filter schema (fields the user can constrain before asking a question)

Agent definitions live as JSON files in agents/ at the project root.
They are created by scripts/create_govil_agent.py (or by hand).

Filter semantics (agent field "type"):
- "choice" / "text": exact string match on the chunk metadata key
- "date": range filter; chunks store <Field>_int as YYYYMMDD integers because
  Chroma only supports $gte/$lte on numbers.
"""
import json
import threading
from pathlib import Path

from langchain_chroma import Chroma

from config import CHROMA_DB_DIR

AGENTS_DIR = Path(__file__).parent.parent / "agents"

_agent_stores: dict[str, Chroma] = {}
_stores_lock = threading.Lock()


def list_agents() -> list[dict]:
    if not AGENTS_DIR.exists():
        return []
    agents = []
    for path in sorted(AGENTS_DIR.glob("*.json")):
        with open(path, encoding="utf-8") as f:
            agents.append(json.load(f))
    return agents


def get_agent(agent_id: str) -> dict | None:
    path = AGENTS_DIR / f"{agent_id}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_agent_vector_store(agent_id: str) -> Chroma:
    """Return (and cache) the Chroma store for an agent's collection."""
    agent = get_agent(agent_id)
    if agent is None:
        raise ValueError(f"Unknown agent: {agent_id}")
    collection = agent["chroma_collection"]
    if collection in _agent_stores:
        return _agent_stores[collection]
    with _stores_lock:
        if collection not in _agent_stores:
            from src.embeddings import get_embedding_model
            _agent_stores[collection] = Chroma(
                collection_name=collection,
                embedding_function=get_embedding_model(),
                persist_directory=str(CHROMA_DB_DIR),
            )
        return _agent_stores[collection]


def invalidate_agent_store(agent_id: str) -> None:
    agent = get_agent(agent_id)
    if agent:
        with _stores_lock:
            _agent_stores.pop(agent["chroma_collection"], None)


def _date_to_int(value: str) -> int:
    """'2025-06-11' / '2025-06-11T00:00:00+03:00' -> 20250611"""
    return int(value[:10].replace("-", ""))


def build_chroma_where(agent: dict, filters: dict | None) -> dict | None:
    """Translate UI filters into a Chroma `where` clause.

    filters format (keys matching the agent's filter schema):
      {"Committee": "ירושלים", "PublicityDate": {"from": "2024-01-01", "to": "2025-01-01"}}
    Unknown keys and empty values are ignored.
    """
    if not filters:
        return None
    schema = {f["name"]: f for f in agent.get("filters", [])}
    clauses: list[dict] = []
    for name, value in filters.items():
        field = schema.get(name)
        if field is None or value in (None, "", {}):
            continue
        if field["type"] == "date":
            if not isinstance(value, dict):
                continue
            if value.get("from"):
                clauses.append({f"{name}_int": {"$gte": _date_to_int(value["from"])}})
            if value.get("to"):
                clauses.append({f"{name}_int": {"$lte": _date_to_int(value["to"])}})
        else:
            clauses.append({name: {"$eq": str(value)}})
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}
