"""Pilot test: query the govil agent with and without metadata filters."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.agents import get_agent, build_chroma_where
from src.retriever import retrieve_for_query

AGENT_ID = "decisive_appraisal_decisions"
agent = get_agent(AGENT_ID)

print("=== 1. Unfiltered retrieval on agent collection ===")
docs = retrieve_for_query("היטל השבחה בגין זכויות בניה", agent_id=AGENT_ID)
committees = {d.metadata.get("Committee") for d in docs}
print(f"retrieved {len(docs)} chunks, committees: {committees}")

print("\n=== 2. Filtered: Committee=ירושלים ===")
where = build_chroma_where(agent, {"Committee": "ירושלים"})
print(f"where: {where}")
docs = retrieve_for_query("היטל השבחה בגין זכויות בניה", agent_id=AGENT_ID, where=where)
committees = {d.metadata.get("Committee") for d in docs}
print(f"retrieved {len(docs)} chunks, committees: {committees}")
assert committees <= {"ירושלים"}, "FILTER LEAK!"

print("\n=== 3. Filtered: date range 2026 only ===")
where = build_chroma_where(agent, {"PublicityDate": {"from": "2026-01-01", "to": "2026-12-31"}})
print(f"where: {where}")
docs = retrieve_for_query("היטל השבחה", agent_id=AGENT_ID, where=where)
dates = sorted({d.metadata.get("PublicityDate") for d in docs})
print(f"retrieved {len(docs)} chunks, publicity dates: {dates}")
assert all(d >= "2026-01-01" for d in dates), "DATE FILTER LEAK!"

print("\nALL RETRIEVAL TESTS PASSED")
