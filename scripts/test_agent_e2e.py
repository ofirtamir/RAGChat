"""End-to-end pilot: full pipeline answer with agent + filters."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.graph import run_rag_pipeline

result = run_rag_pipeline(
    query="מה הוחלט לגבי היטל ההשבחה? תן סכומים אם יש",
    agent_id="decisive_appraisal_decisions",
    filters={"Committee": "ירושלים"},
)

print("ANSWER (first 800 chars):")
print(result["answer"][:800])
print("\nSOURCES:")
for s in result["sources"]:
    print(" -", s[:100])
