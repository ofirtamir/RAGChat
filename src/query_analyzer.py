"""
Query Analyzer: combined router + rewriter in a single LLM call.

Decides whether the query needs document retrieval (routing) AND
optimizes it for vector search (rewriting) — all in one pass.
Replaces the separate Router and Rewriter nodes to save an LLM call.
"""

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.messages import HumanMessage, AIMessage
from pydantic import BaseModel, Field

from config import GOOGLE_API_KEY, LLM_MODEL, LLM_TIMEOUT_SHORT, LLM_MAX_RETRIES
from src.prompt_store import load_prompt


class QueryAnalysis(BaseModel):
    """Combined routing + rewriting result from a single LLM call."""
    needs_retrieval: bool = Field(
        description="True if the query requires searching documents, False for chitchat"
    )
    rewritten_queries: list[str] = Field(
        description="Optimized search queries for vector retrieval. Empty list if needs_retrieval is False."
    )
    reasoning: str = Field(
        description="Brief explanation of the routing and rewriting decision"
    )


ANALYZER_SYSTEM_PROMPT = """You are a query analyzer for a RAG (Retrieval-Augmented Generation) system.
You perform TWO tasks in one step:

## TASK 1: ROUTING — Does this query need document retrieval?

Classify as CHITCHAT (needs_retrieval: false) if:
- Greetings: "hi", "hello", "how are you", "thanks", "bye"
- General knowledge questions unrelated to documents
- Follow-up pleasantries or small talk
- Questions about the assistant itself ("what can you do?", "who are you?")
- Simple acknowledgments ("ok", "got it", "thanks")
- **Formatting/transformation requests on previous answers**: "put this in a table", "convert to a list",
  "reformat this", "rewrite this", "make it shorter", "translate this", "organize this differently"
  — when "this" clearly refers to content already present in the conversation history
- **Follow-up processing of previous answers**: "now summarize this", "extract the key points",
  "highlight the important parts" — when the content is already in the chat history

Classify as NEEDS RETRIEVAL (needs_retrieval: true) if:
- The user asks about content, topics, or information that could be in their uploaded documents
- Questions about specific subjects, data, facts from documents
- Requests to summarize, compare, or analyze NEW document content (not already in the conversation)
- Follow-up questions that need ADDITIONAL information not present in the conversation history

KEY RULE: If the answer can be fully constructed from the conversation history,
classify as CHITCHAT. Only classify as NEEDS RETRIEVAL when new information from documents is required.

## TASK 2: REWRITING — Optimize for vector search (ONLY if needs_retrieval is true)

If needs_retrieval is true:
1. Remove conversational filler ("can you tell me", "I want to know", etc.)
2. Extract core topic/keywords that would match document content
3. If the query references chat history, resolve references into standalone queries
4. Generate 1-3 focused search queries (usually 1 is enough, use more for broad questions)
5. Keep queries concise — focus on key terms and concepts
6. Preserve important qualifiers (dates, names, specific terms)
7. Write queries in the same language as the original question

If needs_retrieval is false:
- Return an empty list for rewritten_queries

## EXAMPLES

- "שלום, מה שלומך?" → {{"needs_retrieval": false, "rewritten_queries": [], "reasoning": "Greeting"}}
- "תעשה לי טבלה מזה" → {{"needs_retrieval": false, "rewritten_queries": [], "reasoning": "Formatting request on previous answer"}}
- "מה כתוב במסמך על מלחמות ישראל?" → {{"needs_retrieval": true, "rewritten_queries": ["מלחמות ישראל"], "reasoning": "Document content question"}}
- "What's the difference between TCP and UDP?" → {{"needs_retrieval": true, "rewritten_queries": ["TCP protocol", "UDP protocol"], "reasoning": "Comparison requiring multiple retrievals"}}
- "How many employees and what's the revenue?" → {{"needs_retrieval": true, "rewritten_queries": ["number of employees", "company revenue"], "reasoning": "Multi-topic question"}}

Return valid JSON:
{{"needs_retrieval": boolean, "rewritten_queries": ["query1", ...], "reasoning": "brief explanation"}}

Return ONLY the JSON object, no markdown fences or extra text."""


def _get_analyzer_llm() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=LLM_MODEL,
        google_api_key=GOOGLE_API_KEY,
        temperature=0,
        timeout=LLM_TIMEOUT_SHORT,
        max_retries=LLM_MAX_RETRIES,
    )


def analyze_query(query: str, chat_history: list[dict] | None = None) -> QueryAnalysis:
    """Route and rewrite a query in a single LLM call."""
    llm = _get_analyzer_llm()

    # Build history as Message objects (avoids template variable issues with {})
    history_messages = []
    if chat_history:
        for msg in chat_history[-6:]:
            content = msg["content"][:200]
            if msg["role"] == "user":
                history_messages.append(HumanMessage(content=content))
            else:
                history_messages.append(AIMessage(content=content))

    # Pull the active prompt from Langfuse (label="production"), falling back
    # to the hard-coded literal above if Langfuse is unavailable.
    system_prompt = load_prompt("analyzer-system", fallback=ANALYZER_SYSTEM_PROMPT)

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        MessagesPlaceholder("chat_history"),
        ("human", "{query}"),
    ])

    parser = JsonOutputParser(pydantic_object=QueryAnalysis)
    chain = prompt | llm | parser

    result = chain.invoke({"query": query, "chat_history": history_messages})
    return QueryAnalysis(**result)
