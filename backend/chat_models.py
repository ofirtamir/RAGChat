"""Request and response bodies of the chat endpoints (backend/api.py)."""
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    query: str
    chat_history: list[dict] = []
    session_id: str | None = None
    agent_id: str | None = None
    filters: dict | None = None
    # The previous answer's `auto_filters.inherit`, sent back so a follow-up
    # ("באותה החלטה") keeps them. Each item is validated in src/filter_conditions.
    inherited_filters: list[dict] | None = Field(default=None, max_length=20)
    # Fields of found filters the user removed from the chips.
    disabled_filters: list[str] = Field(default_factory=list, max_length=20)

    def filter_args(self) -> dict:
        """Keyword arguments for run_rag_pipeline / stream_rag_pipeline."""
        return {"filters": self.filters, "inherited_filters": self.inherited_filters,
                "disabled_filters": [f[:64] for f in self.disabled_filters]}


class ChatResponse(BaseModel):
    answer: str
    sources: list[str]
    citations: list[dict] = []
    route: dict | None
    rewritten: dict | None
    full_doc_decision: dict | None
    plan: dict | None
    question_id: str | None = None
    auto_filters: dict | None = None
