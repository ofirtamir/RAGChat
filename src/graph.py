"""
LangGraph RAG pipeline with:
- Query Analyzer: routing + query rewriting in a single LLM call
- Full Doc Detector: detects if entire document is needed
- Retriever: chunk-based OR full-document retrieval
- Generator: answer generation with conversation history
"""

from typing import TypedDict
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import StateGraph, END

from src.router import RouteDecision
from src.query_rewriter import create_fallback_rewrite, RewrittenQuery
from src.query_analyzer import analyze_query, QueryAnalysis
from src.full_doc_detector import detect_full_document_need, create_fallback_full_doc_decision, FullDocDecision
from src.planner import create_fallback_plan, QueryPlan
from src.retriever import retrieve_for_query
from src.vector_store import get_document_count, list_document_sources, get_all_chunks_for_source
from config import (
    GOOGLE_API_KEY, LLM_MODEL, LLM_TEMPERATURE, LLM_MAX_OUTPUT_TOKENS,
    LLM_TIMEOUT_LONG, LLM_MAX_RETRIES,
)


# ──────────────────────────────────────────────
# State
# ──────────────────────────────────────────────

class GraphState(TypedDict):
    """State that flows through the RAG graph."""
    query: str                              # Original user query
    chat_history: list[dict]                # [{"role": "user"|"assistant", "content": "..."}]
    route: RouteDecision | None
    rewritten: RewrittenQuery | None        # Optimized search queries
    full_doc_decision: FullDocDecision | None  # Full document retrieval decision
    plan: QueryPlan | None
    documents: list[Document]
    answer: str
    sources: list[str]
    skip_generation: bool                   # When True, LLM nodes skip generation (for streaming)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _get_llm() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=LLM_MODEL,
        google_api_key=GOOGLE_API_KEY,
        temperature=LLM_TEMPERATURE,
        max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        timeout=LLM_TIMEOUT_LONG,
        max_retries=LLM_MAX_RETRIES,
    )


def _format_docs(docs: list[Document]) -> str:
    if not docs:
        return "No relevant documents found."
    formatted = []
    for i, doc in enumerate(docs, 1):
        source = doc.metadata.get("source", "unknown")
        page = doc.metadata.get("page", "")
        page_str = f" (page {page + 1})" if page != "" else ""
        formatted.append(f"[{i}] Source: {source}{page_str}\n{doc.page_content}")
    return "\n\n---\n\n".join(formatted)


def _extract_sources(docs: list[Document]) -> list[str]:
    return list({doc.metadata.get("source", "unknown") for doc in docs})


def _extract_citations(docs: list[Document], snippet_len: int = 150) -> list[dict]:
    """Return ordered citation details matching [1], [2], … numbering in _format_docs."""
    citations = []
    for doc in docs:
        source = doc.metadata.get("source", "unknown")
        snippet = doc.page_content[:snippet_len].strip()
        citations.append({"source": source, "snippet": snippet})
    return citations


def _build_history_messages(chat_history: list[dict], limit: int = 10) -> list:
    """Convert chat history to LangChain Message objects (NOT tuples).
    Using Message objects avoids template variable interpolation issues
    when chat content contains curly braces {} or special characters."""
    messages = []
    for msg in chat_history[-limit:]:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        else:
            messages.append(AIMessage(content=msg["content"]))
    return messages


# ──────────────────────────────────────────────
# Node: Query Analyzer (routing + rewriting)
# ──────────────────────────────────────────────

def analyze_query_node(state: GraphState) -> dict:
    """Route and rewrite the query in a single LLM call."""
    try:
        analysis = analyze_query(state["query"], state.get("chat_history", []))

        route = RouteDecision(
            needs_retrieval=analysis.needs_retrieval,
            reasoning=analysis.reasoning,
        )

        if analysis.needs_retrieval and analysis.rewritten_queries:
            rewritten = RewrittenQuery(
                original_query=state["query"],
                rewritten_queries=analysis.rewritten_queries,
                reasoning=analysis.reasoning,
            )
        else:
            rewritten = create_fallback_rewrite(state["query"])

        return {"route": route, "rewritten": rewritten}

    except Exception:
        # Fallback: assume retrieval needed, use original query
        return {
            "route": RouteDecision(needs_retrieval=True, reasoning="Fallback: assuming retrieval needed."),
            "rewritten": create_fallback_rewrite(state["query"]),
        }


# ──────────────────────────────────────────────
# Node: Chitchat
# ──────────────────────────────────────────────

APPRAISER_PERSONA = """אתה שמאי מקרקעין מנוסה עם ידע רחב בשמאות, הערכות שווי, חוות דעת שמאיות ודיני תכנון ובנייה בישראל.
אתה משתמש בשפה מקצועית של שמאים — מונחים כמו שווי שוק, גישת ההשוואה, גישת היוון ההכנסות, גישת העלות,
היטל השבחה, תב"ע, זכויות בנייה, שטח עיקרי/שירות, מקדם תאימות, פחת, ניצול, ייעוד קרקע, וכו'.
כשאתה עונה — ענה כמו שמאי מדבר עם שמאי: ישיר, מקצועי, תכלס, עם שימוש טבעי במונחי שמאות.
ענה תמיד בעברית אלא אם המשתמש פונה בשפה אחרת.
השתמש ב-Markdown מסודר (טבלאות, רשימות, כותרות) כשמתאים."""

CHITCHAT_SYSTEM_PROMPT = f"""{APPRAISER_PERSONA}

אתה מטפל כרגע בפנייה שלא דורשת חיפוש במסמכים — המידע הנדרש הוא ידע כללי בשמאות או שהוא כבר נמצא בהיסטוריית השיחה.

חובה להשתמש בהיסטוריית השיחה כדי לענות על בקשות המשך. אם המשתמש מבקש לעצב מחדש, לסכם, להמיר לטבלה
או לשנות תוכן מהודעה קודמת — השתמש בתוכן המלא מההיסטוריה."""


def chitchat_node(state: GraphState) -> dict:
    result = {
        "plan": create_fallback_plan(state["query"]),
        "rewritten": create_fallback_rewrite(state["query"]),
        "full_doc_decision": create_fallback_full_doc_decision(),
        "documents": [],
        "sources": [],
    }

    # Skip LLM call when streaming (tokens will be streamed separately)
    if state.get("skip_generation"):
        return result

    try:
        llm = _get_llm()
        prompt = ChatPromptTemplate.from_messages([
            ("system", CHITCHAT_SYSTEM_PROMPT),
            MessagesPlaceholder("chat_history"),
            ("human", "{query}"),
        ])
        chain = prompt | llm | StrOutputParser()
        history = _build_history_messages(state.get("chat_history", []))
        result["answer"] = chain.invoke({"query": state["query"], "chat_history": history})
    except Exception:
        result["answer"] = (
            "⚠️ מצטער, לא הצלחתי לייצר תשובה כרגע. "
            "ייתכן שיש עומס על השרת — נסה שוב בעוד כמה שניות."
        )
    return result


# ──────────────────────────────────────────────
# Node: Check Documents
# ──────────────────────────────────────────────

def check_documents_node(state: GraphState) -> dict:
    if get_document_count() == 0:
        return {
            "answer": "No documents have been uploaded yet. Please upload documents first using the sidebar.",
            "plan": create_fallback_plan(state["query"]),
            "rewritten": create_fallback_rewrite(state["query"]),
            "full_doc_decision": create_fallback_full_doc_decision(),
            "documents": [],
            "sources": [],
        }
    return {}


# ──────────────────────────────────────────────
# Node: Full Document Detector
# ──────────────────────────────────────────────

def full_doc_detector_node(state: GraphState) -> dict:
    """Detect whether the query needs full document retrieval."""
    try:
        available = list_document_sources()
        rewritten = state.get("rewritten")
        search_query = rewritten.rewritten_queries[0] if rewritten else state["query"]

        decision = detect_full_document_need(
            query=search_query,
            available_sources=available,
            chat_history=state.get("chat_history", []),
        )
    except Exception:
        decision = create_fallback_full_doc_decision()

    return {"full_doc_decision": decision}


# ──────────────────────────────────────────────
# Node: Full Document Retriever
# ──────────────────────────────────────────────

def full_doc_retriever_node(state: GraphState) -> dict:
    """Retrieve ALL chunks for the identified document(s)."""
    decision = state["full_doc_decision"]
    all_docs = []

    for source in decision.target_sources:
        chunks = get_all_chunks_for_source(source)
        all_docs.extend(chunks)

    return {
        "documents": all_docs,
        "sources": _extract_sources(all_docs),
        # Set a full-doc plan so generator knows to treat this as a synthesis task
        "plan": QueryPlan(
            is_complex=True,
            sub_queries=decision.target_sources,
            reasoning=f"Full document retrieval for: {', '.join(decision.target_sources)}",
        ),
    }


# ──────────────────────────────────────────────
# Node: Retriever (chunk-based)
# ──────────────────────────────────────────────

def retriever_node(state: GraphState) -> dict:
    """Retrieve documents using rewritten queries.
    Also builds a QueryPlan from the rewriter output (no LLM call)."""
    rewritten = state.get("rewritten")
    queries = rewritten.rewritten_queries if rewritten else [state["query"]]

    # Build plan deterministically from rewriter output
    is_complex = len(queries) > 1
    plan = QueryPlan(
        is_complex=is_complex,
        sub_queries=queries,
        reasoning=f"{'פירוק מהשכתוב' if is_complex else 'שאילתה ישירה'}: {len(queries)} שאילתות חיפוש.",
    )

    all_docs = []
    for sq in queries:
        docs = retrieve_for_query(sq)
        all_docs.extend(docs)

    seen = set()
    unique_docs = []
    for doc in all_docs:
        if doc.page_content not in seen:
            seen.add(doc.page_content)
            unique_docs.append(doc)

    return {
        "plan": plan,
        "documents": unique_docs,
        "sources": _extract_sources(unique_docs),
    }


# ──────────────────────────────────────────────
# Node: Generator
# ──────────────────────────────────────────────

ANSWER_SYSTEM_PROMPT = f"""{APPRAISER_PERSONA}

ענה על השאלה אך ורק על סמך ההקשר מהמסמכים להלן. אם אין מספיק מידע בהקשר — אמור זאת בבירור.
אל תמציא מידע. ציין את מסמך המקור באמצעות מספרי סימוכין [1], [2] וכו'.

הקשר:
{{context}}"""

FULL_DOC_SYSTEM_PROMPT = f"""{APPRAISER_PERSONA}

קיבלת את התוכן המלא של המסמך/ים להלן. תן תשובה מקיפה ומובנית היטב.
ארגן את התשובה עם כותרות וסעיפים ברורים. היה יסודי אך תמציתי.

{{context}}"""

SYNTHESIS_SYSTEM_PROMPT = f"""{APPRAISER_PERSONA}

להלן תוצאות מחיפושים מרובים בתת-שאילתות שונות. סנתז את המידע לתשובה אחת מגובשת ומקיפה.
השתמש אך ורק במידע שסופק.

{{sub_query_results}}"""


def generator_node(state: GraphState) -> dict:
    if state.get("answer") or state.get("skip_generation"):
        return {}

    plan = state["plan"]
    docs = state["documents"]
    query = state["query"]
    chat_history = state.get("chat_history", [])
    full_doc_decision = state.get("full_doc_decision")
    llm = _get_llm()
    history = _build_history_messages(chat_history)

    is_full_doc = (
        full_doc_decision is not None
        and full_doc_decision.needs_full_document
        and full_doc_decision.target_sources
    )

    try:
        if is_full_doc:
            context_parts = []
            for source in full_doc_decision.target_sources:
                source_docs = [d for d in docs if d.metadata.get("source") == source]
                context_parts.append(
                    f"=== Document: {source} ===\n{_format_docs(source_docs)}"
                )
            context = "\n\n".join(context_parts)

            prompt = ChatPromptTemplate.from_messages([
                ("system", FULL_DOC_SYSTEM_PROMPT),
                MessagesPlaceholder("chat_history"),
                ("human", "{query}"),
            ])
            chain = prompt | llm | StrOutputParser()
            answer = chain.invoke({"context": context, "query": query, "chat_history": history})

        elif not plan.is_complex:
            context = _format_docs(docs)

            prompt = ChatPromptTemplate.from_messages([
                ("system", ANSWER_SYSTEM_PROMPT),
                MessagesPlaceholder("chat_history"),
                ("human", "{query}"),
            ])
            chain = prompt | llm | StrOutputParser()
            answer = chain.invoke({"context": context, "query": query, "chat_history": history})

        else:
            sub_results = []
            chunk_start = 0
            for sq in plan.sub_queries:
                chunk_end = chunk_start + 5
                sq_docs = docs[chunk_start:chunk_end]
                sub_results.append(f"Sub-query: {sq}\nRetrieved context:\n{_format_docs(sq_docs)}")
                chunk_start = chunk_end

            combined = "\n\n===\n\n".join(sub_results)

            prompt = ChatPromptTemplate.from_messages([
                ("system", SYNTHESIS_SYSTEM_PROMPT),
                MessagesPlaceholder("chat_history"),
                ("human", "{query}"),
            ])
            chain = prompt | llm | StrOutputParser()
            answer = chain.invoke({"sub_query_results": combined, "query": query, "chat_history": history})

    except Exception:
        answer = (
            "⚠️ מצטער, לא הצלחתי לייצר תשובה כרגע. "
            "ייתכן שיש עומס על השרת — נסה שוב בעוד כמה שניות."
        )

    return {"answer": answer}


# ──────────────────────────────────────────────
# Conditional Edges
# ──────────────────────────────────────────────

def route_after_analyzer(state: GraphState) -> str:
    route = state.get("route")
    if route and not route.needs_retrieval:
        return "chitchat"
    return "check_documents"


def route_after_check_documents(state: GraphState) -> str:
    if state.get("answer"):
        return END
    return "full_doc_detector"


def route_after_full_doc_detector(state: GraphState) -> str:
    decision = state.get("full_doc_decision")
    if decision and decision.needs_full_document and decision.target_sources:
        return "full_doc_retriever"
    return "retriever"


# ──────────────────────────────────────────────
# Build Graph
# ──────────────────────────────────────────────

def build_rag_graph() -> StateGraph:
    workflow = StateGraph(GraphState)

    workflow.add_node("analyzer", analyze_query_node)
    workflow.add_node("chitchat", chitchat_node)
    workflow.add_node("check_documents", check_documents_node)
    workflow.add_node("full_doc_detector", full_doc_detector_node)
    workflow.add_node("full_doc_retriever", full_doc_retriever_node)
    workflow.add_node("retriever", retriever_node)
    workflow.add_node("generator", generator_node)

    workflow.set_entry_point("analyzer")

    # analyzer → chitchat OR check_documents
    workflow.add_conditional_edges(
        "analyzer",
        route_after_analyzer,
        {"chitchat": "chitchat", "check_documents": "check_documents"},
    )

    workflow.add_edge("chitchat", END)

    # check_documents → full_doc_detector OR END (no docs)
    workflow.add_conditional_edges(
        "check_documents",
        route_after_check_documents,
        {"full_doc_detector": "full_doc_detector", END: END},
    )

    # full_doc_detector → full_doc_retriever OR retriever
    workflow.add_conditional_edges(
        "full_doc_detector",
        route_after_full_doc_detector,
        {"full_doc_retriever": "full_doc_retriever", "retriever": "retriever"},
    )

    # full_doc_retriever → generator (skip retriever)
    workflow.add_edge("full_doc_retriever", "generator")

    # retriever → generator
    workflow.add_edge("retriever", "generator")
    workflow.add_edge("generator", END)

    return workflow.compile()


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

_rag_graph = build_rag_graph()


# ──────────────────────────────────────────────
# Node labels in Hebrew for the UI
# ──────────────────────────────────────────────
NODE_LABELS = {
    "analyzer":          {"label": "מנתח ומשכתב את השאילתה",   "icon": "🧭"},
    "chitchat":          {"label": "מייצר תגובת שיחה",         "icon": "💬"},
    "check_documents":   {"label": "בודק זמינות מסמכים",       "icon": "📋"},
    "full_doc_detector": {"label": "בודק אם נדרש מסמך מלא",   "icon": "🔍"},
    "full_doc_retriever":{"label": "מאחזר מסמך מלא",           "icon": "📄"},
    "retriever":         {"label": "מחפש מידע רלוונטי",        "icon": "🗂️"},
    "generator":         {"label": "מייצר תשובה",              "icon": "⚡"},
}


def _build_config(session_id: str | None, user_id: str | None, query: str) -> tuple[dict, object]:
    """
    Build LangGraph invoke/stream config with optional Langfuse handler.
    Returns (config_dict, handler_or_None) — caller should flush() the handler
    after the pipeline completes so batched events are sent immediately.
    """
    from src.observability import get_langfuse_handler

    config: dict = {}
    handler, langfuse_metadata = get_langfuse_handler(
        session_id=session_id,
        user_id=user_id,
        trace_name="rag-pipeline",
        metadata={"query": query},
    )
    if handler:
        config["callbacks"] = [handler]
        config["metadata"] = langfuse_metadata
    return config, handler


def _build_initial_state(query: str, chat_history: list[dict] | None, skip_generation: bool = False) -> GraphState:
    return {
        "query": query,
        "chat_history": chat_history or [],
        "route": None,
        "rewritten": None,
        "full_doc_decision": None,
        "plan": None,
        "documents": [],
        "answer": "",
        "sources": [],
        "skip_generation": skip_generation,
    }


def _build_result(result: dict, query: str) -> dict:
    return {
        "answer": result["answer"],
        "plan": result.get("plan") or create_fallback_plan(query),
        "sources": result.get("sources", []),
        "citations": _extract_citations(result.get("documents", [])),
        "route": result.get("route"),
        "rewritten": result.get("rewritten"),
        "full_doc_decision": result.get("full_doc_decision"),
    }


def run_rag_pipeline(
    query: str,
    chat_history: list[dict] | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
) -> dict:
    """Main entry point for the LangGraph RAG pipeline (non-streaming)."""
    from src.observability import start_trace_span, flush_langfuse

    initial_state = _build_initial_state(query, chat_history)
    config, lf_handler = _build_config(session_id, user_id, query)

    try:
        # Wrap the whole pipeline in an explicit Langfuse span so user_id /
        # session_id are attached to the parent trace (the LangChain
        # callback alone does not propagate them up to the trace).
        with start_trace_span(
            name="rag-pipeline",
            user_id=user_id,
            session_id=session_id,
            input_data={"query": query},
        ):
            result = _rag_graph.invoke(initial_state, config=config)
        return _build_result(result, query)
    finally:
        flush_langfuse()


def _build_llm_chain(state: dict, node_name: str):
    """
    Build the LLM chain and input dict for token-by-token streaming.
    Returns (chain, input_dict) tuple.
    Uses MessagesPlaceholder for chat history to avoid template variable issues.
    """
    llm = _get_llm()
    query = state["query"]
    chat_history = state.get("chat_history", [])
    history = _build_history_messages(chat_history)

    if node_name == "chitchat":
        prompt = ChatPromptTemplate.from_messages([
            ("system", CHITCHAT_SYSTEM_PROMPT),
            MessagesPlaceholder("chat_history"),
            ("human", "{query}"),
        ])
        chain = prompt | llm | StrOutputParser()
        return chain, {"query": query, "chat_history": history}

    # Generator modes
    plan = state["plan"]
    docs = state["documents"]
    full_doc_decision = state.get("full_doc_decision")

    is_full_doc = (
        full_doc_decision is not None
        and full_doc_decision.needs_full_document
        and full_doc_decision.target_sources
    )

    if is_full_doc:
        context_parts = []
        for source in full_doc_decision.target_sources:
            source_docs = [d for d in docs if d.metadata.get("source") == source]
            context_parts.append(
                f"=== Document: {source} ===\n{_format_docs(source_docs)}"
            )
        context = "\n\n".join(context_parts)
        prompt = ChatPromptTemplate.from_messages([
            ("system", FULL_DOC_SYSTEM_PROMPT),
            MessagesPlaceholder("chat_history"),
            ("human", "{query}"),
        ])
        chain = prompt | llm | StrOutputParser()
        return chain, {"context": context, "query": query, "chat_history": history}

    elif not plan.is_complex:
        context = _format_docs(docs)
        prompt = ChatPromptTemplate.from_messages([
            ("system", ANSWER_SYSTEM_PROMPT),
            MessagesPlaceholder("chat_history"),
            ("human", "{query}"),
        ])
        chain = prompt | llm | StrOutputParser()
        return chain, {"context": context, "query": query, "chat_history": history}

    else:
        sub_results = []
        chunk_start = 0
        for sq in plan.sub_queries:
            chunk_end = chunk_start + 5
            sq_docs = docs[chunk_start:chunk_end]
            sub_results.append(f"Sub-query: {sq}\nRetrieved context:\n{_format_docs(sq_docs)}")
            chunk_start = chunk_end
        combined = "\n\n===\n\n".join(sub_results)
        prompt = ChatPromptTemplate.from_messages([
            ("system", SYNTHESIS_SYSTEM_PROMPT),
            MessagesPlaceholder("chat_history"),
            ("human", "{query}"),
        ])
        chain = prompt | llm | StrOutputParser()
        return chain, {"sub_query_results": combined, "query": query, "chat_history": history}


def stream_rag_pipeline(
    query: str,
    chat_history: list[dict] | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
):
    """
    Generator that streams pipeline progress AND LLM tokens.
    Yields dicts:
      {"type": "step",   "node": ..., "label": ..., "icon": ..., "detail": ...}
      {"type": "token",  "content": "..."}   ← individual LLM tokens
      {"type": "result", "data": { full result dict }}
    """
    from src.observability import start_trace_span

    initial_state = _build_initial_state(query, chat_history, skip_generation=True)
    config, lf_handler = _build_config(session_id, user_id, query)

    # LangGraph stream yields (node_name, state_update) dicts
    final_state = dict(initial_state)
    llm_node_name = None  # Track which node should generate the LLM response

    try:
        # Wrap everything in an explicit Langfuse span so user_id /
        # session_id land on the parent trace, not just on child spans.
        with start_trace_span(
            name="rag-pipeline",
            user_id=user_id,
            session_id=session_id,
            input_data={"query": query},
        ):
            for chunk in _rag_graph.stream(initial_state, config=config):
                for node_name, state_update in chunk.items():
                    if isinstance(state_update, dict):
                        final_state.update(state_update)

                    # Skip step event for LLM nodes (we'll stream them manually)
                    if node_name in ("generator", "chitchat") and not final_state.get("answer"):
                        llm_node_name = node_name
                        continue

                    meta = NODE_LABELS.get(node_name, {"label": node_name, "icon": "⚙️"})
                    detail = _get_step_detail(node_name, final_state)
                    yield {
                        "type": "step",
                        "node": node_name,
                        "label": meta["label"],
                        "icon": meta["icon"],
                        "detail": detail,
                    }

            # If answer was already set (e.g., check_documents "no docs" message)
            if final_state.get("answer"):
                yield {"type": "result", "data": _build_result(final_state, query)}
                return

            # Emit the generator/chitchat step event
            target_node = llm_node_name or "generator"
            gen_meta = NODE_LABELS.get(target_node, {"label": "מייצר תשובה", "icon": "⚡"})
            yield {
                "type": "step",
                "node": target_node,
                "label": gen_meta["label"],
                "icon": gen_meta["icon"],
                "detail": "",
            }

            # Build the LLM chain and stream tokens.
            chain, inputs = _build_llm_chain(final_state, target_node)

            full_answer = ""
            for token_chunk in chain.stream(inputs, config=config):
                full_answer += token_chunk
                yield {"type": "token", "content": token_chunk}

            final_state["answer"] = full_answer
            yield {"type": "result", "data": _build_result(final_state, query)}

    finally:
        # Flush Langfuse immediately so batched spans aren't lost when the
        # background worker hasn't fired yet in the ThreadPoolExecutor thread.
        from src.observability import flush_langfuse
        flush_langfuse()


def _get_step_detail(node_name: str, state: dict) -> str:
    """Extract a short human-readable detail for a completed node."""
    try:
        if node_name == "analyzer":
            route = state.get("route")
            rw = state.get("rewritten")
            if route and not route.needs_retrieval:
                return "שיחת חולין"
            elif rw and rw.rewritten_queries:
                return " | ".join(rw.rewritten_queries[:3])
            return "נדרש אחזור מסמכים"
        elif node_name == "full_doc_detector":
            fd = state.get("full_doc_decision")
            if fd:
                if fd.needs_full_document:
                    return f"מסמכים: {', '.join(fd.target_sources[:2])}"
                return "אחזור חלקי (chunks)"
        elif node_name == "retriever":
            docs = state.get("documents", [])
            return f"נמצאו {len(docs)} קטעים רלוונטיים"
        elif node_name == "full_doc_retriever":
            docs = state.get("documents", [])
            return f"אוחזרו {len(docs)} קטעים מהמסמך המלא"
        elif node_name == "generator":
            return "התשובה מוכנה"
        elif node_name == "check_documents":
            if state.get("answer"):
                return "אין מסמכים – נדרשת העלאה"
            return "מסמכים זמינים"
        elif node_name == "chitchat":
            return "תגובה חופשית ללא אחזור"
    except Exception:
        pass
    return ""
