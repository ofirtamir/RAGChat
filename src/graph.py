"""
LangGraph RAG pipeline with:
- Router: chitchat vs. retrieval
- Query Rewriter: optimizes queries for vector search
- Full Doc Detector: detects if entire document is needed
- Planner: simple vs. complex query decomposition
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

from src.router import route_query, RouteDecision
from src.query_rewriter import rewrite_query, create_fallback_rewrite, RewrittenQuery
from src.full_doc_detector import detect_full_document_need, create_fallback_full_doc_decision, FullDocDecision
from src.planner import create_query_plan, create_fallback_plan, QueryPlan
from src.retriever import retrieve_for_query
from src.vector_store import get_document_count, list_document_sources, get_all_chunks_for_source
from config import GOOGLE_API_KEY, LLM_MODEL, LLM_TEMPERATURE, LLM_MAX_OUTPUT_TOKENS


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
# Node: Router
# ──────────────────────────────────────────────

def router_node(state: GraphState) -> dict:
    """Decide if the query needs retrieval or is chitchat."""
    try:
        decision = route_query(state["query"], state.get("chat_history", []))
    except Exception:
        decision = RouteDecision(needs_retrieval=True, reasoning="Fallback: assuming retrieval needed.")
    return {"route": decision}


# ──────────────────────────────────────────────
# Node: Chitchat
# ──────────────────────────────────────────────

CHITCHAT_SYSTEM_PROMPT = """You are RAGChat, a friendly AI assistant specialized in document search and Q&A.
You are handling a request that does NOT require searching documents — the information needed
is either general knowledge or already present in the conversation history.

You MUST use the conversation history to answer follow-up requests. If the user asks to
reformat, restructure, convert to a table, summarize, translate, or otherwise transform
content from a previous message, use the FULL content from the conversation history to do so.

Be helpful, thorough, and accurate. Match the language of the user (if they write in Hebrew, respond in Hebrew).
When creating tables or formatted output, use proper Markdown formatting."""


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

    llm = _get_llm()
    prompt = ChatPromptTemplate.from_messages([
        ("system", CHITCHAT_SYSTEM_PROMPT),
        MessagesPlaceholder("chat_history"),
        ("human", "{query}"),
    ])
    chain = prompt | llm | StrOutputParser()
    history = _build_history_messages(state.get("chat_history", []))
    result["answer"] = chain.invoke({"query": state["query"], "chat_history": history})
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
# Node: Query Rewriter
# ──────────────────────────────────────────────

def rewriter_node(state: GraphState) -> dict:
    try:
        rewritten = rewrite_query(state["query"], state.get("chat_history", []))
    except Exception:
        rewritten = create_fallback_rewrite(state["query"])
    return {"rewritten": rewritten}


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
# Node: Planner
# ──────────────────────────────────────────────

def planner_node(state: GraphState) -> dict:
    rewritten = state.get("rewritten")

    if rewritten and len(rewritten.rewritten_queries) > 1:
        return {
            "plan": QueryPlan(
                is_complex=True,
                sub_queries=rewritten.rewritten_queries,
                reasoning=f"Query rewriter decomposed into {len(rewritten.rewritten_queries)} search queries.",
            )
        }

    search_query = rewritten.rewritten_queries[0] if rewritten else state["query"]
    try:
        plan = create_query_plan(search_query)
    except Exception:
        plan = create_fallback_plan(search_query)
    return {"plan": plan}


# ──────────────────────────────────────────────
# Node: Retriever (chunk-based)
# ──────────────────────────────────────────────

def retriever_node(state: GraphState) -> dict:
    plan = state["plan"]
    all_docs = []

    for sq in plan.sub_queries:
        docs = retrieve_for_query(sq)
        all_docs.extend(docs)

    seen = set()
    unique_docs = []
    for doc in all_docs:
        if doc.page_content not in seen:
            seen.add(doc.page_content)
            unique_docs.append(doc)

    return {
        "documents": unique_docs,
        "sources": _extract_sources(unique_docs),
    }


# ──────────────────────────────────────────────
# Node: Generator
# ──────────────────────────────────────────────

ANSWER_SYSTEM_PROMPT = """You are a helpful assistant that answers questions based on the provided context from documents.
Use ONLY the context below to answer. If the context doesn't contain enough information, say so clearly.
Do not make up information. Cite the source document when possible.

Context:
{context}"""

FULL_DOC_SYSTEM_PROMPT = """You are a helpful assistant performing a comprehensive analysis of complete document(s).
You have been given the FULL content of the document(s) below. Provide a thorough, well-structured answer.
Organize your response with clear sections. Be comprehensive but concise.

{context}"""

SYNTHESIS_SYSTEM_PROMPT = """You are a helpful assistant that synthesizes information from multiple document retrievals.
Below are results from multiple sub-queries. Synthesize into a coherent, comprehensive answer.
Use ONLY the provided information.

{sub_query_results}"""


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

    return {"answer": answer}


# ──────────────────────────────────────────────
# Conditional Edges
# ──────────────────────────────────────────────

def route_after_router(state: GraphState) -> str:
    route = state.get("route")
    if route and not route.needs_retrieval:
        return "chitchat"
    return "check_documents"


def route_after_check_documents(state: GraphState) -> str:
    if state.get("answer"):
        return END
    return "rewriter"


def route_after_full_doc_detector(state: GraphState) -> str:
    decision = state.get("full_doc_decision")
    if decision and decision.needs_full_document and decision.target_sources:
        return "full_doc_retriever"
    return "planner"


# ──────────────────────────────────────────────
# Build Graph
# ──────────────────────────────────────────────

def build_rag_graph() -> StateGraph:
    workflow = StateGraph(GraphState)

    workflow.add_node("router", router_node)
    workflow.add_node("chitchat", chitchat_node)
    workflow.add_node("check_documents", check_documents_node)
    workflow.add_node("rewriter", rewriter_node)
    workflow.add_node("full_doc_detector", full_doc_detector_node)
    workflow.add_node("full_doc_retriever", full_doc_retriever_node)
    workflow.add_node("planner", planner_node)
    workflow.add_node("retriever", retriever_node)
    workflow.add_node("generator", generator_node)

    workflow.set_entry_point("router")

    workflow.add_conditional_edges(
        "router",
        route_after_router,
        {"chitchat": "chitchat", "check_documents": "check_documents"},
    )

    workflow.add_edge("chitchat", END)

    workflow.add_conditional_edges(
        "check_documents",
        route_after_check_documents,
        {"rewriter": "rewriter", END: END},
    )

    # rewriter → full_doc_detector → full_doc_retriever OR planner
    workflow.add_edge("rewriter", "full_doc_detector")

    workflow.add_conditional_edges(
        "full_doc_detector",
        route_after_full_doc_detector,
        {"full_doc_retriever": "full_doc_retriever", "planner": "planner"},
    )

    # full_doc_retriever → generator (skip planner/retriever)
    workflow.add_edge("full_doc_retriever", "generator")

    # planner → retriever → generator
    workflow.add_edge("planner", "retriever")
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
    "router":            {"label": "מנתב את השאילתה",          "icon": "🧭"},
    "chitchat":          {"label": "מייצר תגובת שיחה",         "icon": "💬"},
    "check_documents":   {"label": "בודק זמינות מסמכים",       "icon": "📋"},
    "rewriter":          {"label": "משכתב את השאילתה לחיפוש",  "icon": "✏️"},
    "full_doc_detector": {"label": "בודק אם נדרש מסמך מלא",   "icon": "🔍"},
    "full_doc_retriever":{"label": "מאחזר מסמך מלא",           "icon": "📄"},
    "planner":           {"label": "מתכנן אסטרטגיית חיפוש",   "icon": "📐"},
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
    handler, _ = get_langfuse_handler(
        session_id=session_id,
        user_id=user_id,
        trace_name="rag-pipeline",
        metadata={"query": query},
    )
    if handler:
        config["callbacks"] = [handler]
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
    initial_state = _build_initial_state(query, chat_history)
    config, lf_handler = _build_config(session_id, user_id, query)

    try:
        result = _rag_graph.invoke(initial_state, config=config)
        return _build_result(result, query)
    finally:
        # Flush Langfuse events immediately — avoids losing traces when the
        # SDK's background batch worker hasn't fired yet.
        if lf_handler:
            try:
                lf_handler.flush()
            except Exception:
                pass


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
    initial_state = _build_initial_state(query, chat_history, skip_generation=True)
    config, lf_handler = _build_config(session_id, user_id, query)

    # LangGraph stream yields (node_name, state_update) dicts
    final_state = dict(initial_state)
    llm_node_name = None  # Track which node should generate the LLM response

    try:
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

        # If answer was already set (e.g., check_documents "no docs" message), just return
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

        # Build the LLM chain and stream tokens
        # Pass the same config (with Langfuse callback) so the generation is traced
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
        if lf_handler:
            try:
                lf_handler.flush()
            except Exception:
                pass


def _get_step_detail(node_name: str, state: dict) -> str:
    """Extract a short human-readable detail for a completed node."""
    try:
        if node_name == "router":
            route = state.get("route")
            if route:
                return "שיחת חולין" if not route.needs_retrieval else "נדרש אחזור מסמכים"
        elif node_name == "rewriter":
            rw = state.get("rewritten")
            if rw and rw.rewritten_queries:
                return " | ".join(rw.rewritten_queries[:3])
        elif node_name == "full_doc_detector":
            fd = state.get("full_doc_decision")
            if fd:
                if fd.needs_full_document:
                    return f"מסמכים: {', '.join(fd.target_sources[:2])}"
                return "אחזור חלקי (chunks)"
        elif node_name == "planner":
            plan = state.get("plan")
            if plan:
                return f"{'שאילתה מורכבת' if plan.is_complex else 'שאילתה פשוטה'} ({len(plan.sub_queries)} תת-שאילתות)"
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
