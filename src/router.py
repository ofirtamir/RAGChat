"""
Router node: determines whether a query requires document retrieval
or is just casual conversation (chitchat) that can be answered directly.
"""

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.messages import HumanMessage, AIMessage
from pydantic import BaseModel, Field

from config import GOOGLE_API_KEY, LLM_MODEL


class RouteDecision(BaseModel):
    """Structured output from the router."""
    needs_retrieval: bool = Field(
        description="True if the query requires searching documents, False for chitchat"
    )
    reasoning: str = Field(
        description="Brief explanation of the routing decision"
    )


ROUTER_SYSTEM_PROMPT = """You are a query router for a RAG (Retrieval-Augmented Generation) system.
Your job is to determine whether the user's message requires searching through uploaded documents,
or if it can be answered using the conversation history alone (no new document retrieval needed).

IMPORTANT: Carefully examine the conversation history before deciding.

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
  (e.g., "tell me more about X" when X was only briefly mentioned and more detail is needed from documents)

KEY RULE: If the answer can be fully constructed from the conversation history (previous messages),
classify as CHITCHAT. Only classify as NEEDS RETRIEVAL when new information from documents is required.

Return valid JSON:
{{"needs_retrieval": boolean, "reasoning": "brief explanation"}}

Return ONLY the JSON object, no markdown fences or extra text."""


def _get_router_llm() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=LLM_MODEL,
        google_api_key=GOOGLE_API_KEY,
        temperature=0,
    )


def route_query(query: str, chat_history: list[dict] | None = None) -> RouteDecision:
    """Decide whether a query needs document retrieval or is chitchat."""
    llm = _get_router_llm()

    # Build history as Message objects (NOT tuples) to avoid template variable issues
    history_messages = []
    if chat_history:
        for msg in chat_history[-6:]:  # Last 6 messages for context
            if msg["role"] == "user":
                history_messages.append(HumanMessage(content=msg["content"]))
            else:
                history_messages.append(AIMessage(content=msg["content"]))

    prompt = ChatPromptTemplate.from_messages([
        ("system", ROUTER_SYSTEM_PROMPT),
        MessagesPlaceholder("chat_history"),
        ("human", "{query}"),
    ])
    parser = JsonOutputParser(pydantic_object=RouteDecision)
    chain = prompt | llm | parser

    result = chain.invoke({"query": query, "chat_history": history_messages})
    return RouteDecision(**result)
