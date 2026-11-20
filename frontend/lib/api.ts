import { getSession as getAuthSession } from "next-auth/react"
import { AutoFilters, CitationDetail, FilterCondition, ThinkingStep } from "@/types/chat"
import { redirectToSignIn } from "@/lib/auth-redirect"

// Backend base URL (override via NEXT_PUBLIC_BACKEND_ORIGIN, e.g. http://localhost:8000)
const BACKEND_ORIGIN =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_BACKEND_ORIGIN) ||
  "http://localhost:8000"

const API_BASE = `${BACKEND_ORIGIN}/api`

export interface ChatApiResponse {
  answer: string
  sources: string[]
  citations?: CitationDetail[]
  route: { needs_retrieval: boolean; reasoning: string } | null
  rewritten: { original_query: string; rewritten_queries: string[]; reasoning: string } | null
  full_doc_decision: { needs_full_document: boolean; target_sources: string[]; reasoning: string } | null
  plan: { is_complex: boolean; sub_queries: string[]; reasoning: string } | null
  question_id?: string | null
  auto_filters?: AutoFilters | null
}

/** Filter state carried between turns: the previous answer's filters (kept by a
 *  follow-up question) and the found-filter fields the user removed. */
export interface FilterCarry {
  inheritedFilters?: FilterCondition[] | null
  disabledFilters?: string[]
}

/**
 * Build common request headers, attaching the Google ID token as a Bearer
 * token when the caller has one. The FastAPI backend authenticates every
 * request by verifying this token against Google's public keys — without
 * it, the backend rejects the request with 401.
 */
function authHeaders(token: string | undefined, extra?: HeadersInit): HeadersInit {
  const headers: Record<string, string> = { ...(extra as Record<string, string>) }
  if (token) headers["Authorization"] = `Bearer ${token}`
  return headers
}

/** Thrown when the backend keeps answering 401 and the user must sign in again. */
export class AuthExpiredError extends Error {
  constructor() {
    super("פג תוקף ההתחברות, יש להתחבר מחדש")
    this.name = "AuthExpiredError"
  }
}

// A caller can keep using a token after it expired (a long-lived closure, or a
// tab that just woke from sleep). Remember the replacement we fetched for it so
// later calls with the same stale token skip the failed round trip.
let replacement: { stale: string | undefined; fresh: string } | null = null
let pendingSessionToken: Promise<string | null> | null = null

/** The ID token of the current NextAuth session; fetching it renews the token server-side. */
function currentSessionToken(): Promise<string | null> {
  pendingSessionToken ??= getAuthSession()
    .then(session => (session && !session.error && session.idToken) || null)
    .catch(() => null)
    .finally(() => { pendingSessionToken = null })
  return pendingSessionToken
}

/**
 * `fetch` against the backend with the Bearer token attached.
 *
 * On 401 the session is re-fetched (which renews an expired Google ID token)
 * and the request is retried once. If the backend still answers 401, or the
 * session can no longer be renewed, the user is sent to sign in again.
 */
export async function authFetch(
  url: string,
  token: string | undefined,
  init: RequestInit = {},
): Promise<Response> {
  const send = (t: string | undefined) =>
    fetch(url, { ...init, headers: authHeaders(t, init.headers) })

  const sent = replacement && replacement.stale === token ? replacement.fresh : token
  const res = await send(sent)
  if (res.status !== 401) return res

  const fresh = await currentSessionToken()
  if (fresh) {
    // Same token back means it was not an expiry. Give a brand-new token a
    // moment before the single retry, in case the backend clock trails Google's.
    if (fresh === sent) await new Promise(resolve => setTimeout(resolve, 1000))
    const retry = await send(fresh)
    if (retry.status !== 401) {
      if (fresh !== token) replacement = { stale: token, fresh }
      return retry
    }
  }

  redirectToSignIn()
  throw new AuthExpiredError()
}

// ── Agents (knowledge-base per data source) ──────────────────────────────

export interface AgentFilterField {
  name: string
  label: string
  type: "choice" | "text" | "date"
  values?: string[]
}

export interface AgentInfo {
  id: string
  name: string
  description: string
  filters: AgentFilterField[]
  chunk_count: number
}

/** Filter values keyed by field name; dates are {from?, to?} (YYYY-MM-DD). */
export type AgentFilters = Record<string, string | { from?: string; to?: string }>

export async function getAgents(token: string | undefined): Promise<AgentInfo[]> {
  const res = await authFetch(`${API_BASE}/agents`, token)
  if (!res.ok) throw new Error("Failed to fetch agents")
  const data = await res.json()
  return data.agents ?? []
}

export interface MatchingDocuments {
  count: number
  total: number
  documents: { key: string; title: string; date: string | null }[]
}

/** How many decisions the chosen filters leave; with `limit`, the newest of them too. */
export async function getMatchingDocuments(
  agentId: string,
  filters: AgentFilters,
  token: string | undefined,
  limit = 0,
  signal?: AbortSignal,
): Promise<MatchingDocuments> {
  const res = await authFetch(`${API_BASE}/agents/${encodeURIComponent(agentId)}/matching`, token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filters, limit }),
    signal,
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function sendMessage(
  query: string,
  chatHistory: { role: string; content: string }[],
  sessionId: string,
  token: string | undefined,
  agentId?: string | null,
  filters?: AgentFilters | null,
): Promise<ChatApiResponse> {
  const res = await authFetch(`${API_BASE}/chat`, token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      chat_history: chatHistory,
      session_id: sessionId,
      agent_id: agentId || null,
      filters: filters || null,
    }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Unknown error" }))
    throw new Error(err.detail || "Request failed")
  }
  return res.json()
}

/**
 * Stream the RAG pipeline via SSE (calls backend directly to avoid proxy buffering).
 * Calls onStep for each pipeline stage, returns the final result.
 */
export async function sendMessageStreaming(
  query: string,
  chatHistory: { role: string; content: string }[],
  sessionId: string,
  onStep: (step: ThinkingStep) => void,
  onToken: (token: string) => void,
  token: string | undefined,
  agentId?: string | null,
  filters?: AgentFilters | null,
  carry?: FilterCarry,
): Promise<ChatApiResponse> {
  const res = await authFetch(`${API_BASE}/chat/stream`, token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      chat_history: chatHistory,
      session_id: sessionId,
      agent_id: agentId || null,
      filters: filters || null,
      inherited_filters: carry?.inheritedFilters?.length ? carry.inheritedFilters : null,
      disabled_filters: carry?.disabledFilters ?? [],
    }),
  })

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Unknown error" }))
    throw new Error(err.detail || "Request failed")
  }

  const reader = res.body?.getReader()
  if (!reader) throw new Error("No response body")

  const decoder = new TextDecoder()
  let buffer = ""
  let result: ChatApiResponse | null = null
  let pendingEvent = ""

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })

    // Process complete lines
    while (true) {
      const newlineIdx = buffer.indexOf("\n")
      if (newlineIdx === -1) break

      const line = buffer.slice(0, newlineIdx).trim()
      buffer = buffer.slice(newlineIdx + 1)

      if (line === "") {
        // Empty line = end of SSE message block
        pendingEvent = ""
        continue
      }

      if (line.startsWith("event:")) {
        pendingEvent = line.slice(6).trim()
      } else if (line.startsWith("data:")) {
        const dataStr = line.slice(5).trim()
        if (!dataStr) continue

        try {
          const parsed = JSON.parse(dataStr)

          if (pendingEvent === "step") {
            onStep({
              node: parsed.node,
              label: parsed.label,
              icon: parsed.icon,
              detail: parsed.detail || "",
              status: "done",
            })
          } else if (pendingEvent === "token") {
            onToken(parsed.content || "")
          } else if (pendingEvent === "result") {
            result = parsed as ChatApiResponse
          } else if (pendingEvent === "error") {
            throw new Error(parsed.error || "Pipeline error")
          }
        } catch (e) {
          if (e instanceof SyntaxError) continue
          throw e
        }
      }
    }
  }

  if (!result) throw new Error("No result received from stream")
  return result
}

/** Rate an answer: 1 helpful, -1 not helpful, 0 withdraw. */
export async function sendFeedback(
  questionId: string,
  rating: 1 | -1 | 0,
  comment: string | undefined,
  token: string | undefined,
): Promise<void> {
  const res = await authFetch(`${API_BASE}/questions/${encodeURIComponent(questionId)}/feedback`, token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rating, comment: comment || null }),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
}

export interface SourceDocument {
  record_key: string
  title: string
  pages: number | null
  files: { url: string; name: string; ext: string }[]
  parsed_pages: { number: number; markdown: string }[]
}

/** A cited library document — parsed pages plus links to the original files. */
export async function getSourceDocument(
  agentId: string,
  key: string,
  token: string | undefined,
): Promise<SourceDocument> {
  const res = await authFetch(
    `${API_BASE}/sources/${encodeURIComponent(agentId)}/${encodeURIComponent(key)}`,
    token,
  )
  if (!res.ok) throw new Error(res.status === 404 ? "המסמך לא נמצא" : "טעינת המסמך נכשלה")
  return res.json()
}

// ── Chat session persistence ─────────────────────────────────────────────

export interface SessionSummary {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export interface StoredSession {
  id: string
  title: string
  messages: unknown[]
  created_at: string
  updated_at: string
}

export async function listSessions(token: string | undefined): Promise<SessionSummary[]> {
  const res = await authFetch(`${API_BASE}/sessions`, token)
  if (!res.ok) throw new Error("Failed to list sessions")
  const data = await res.json()
  return data.sessions ?? []
}

export async function getSession(
  sessionId: string,
  token: string | undefined,
): Promise<StoredSession> {
  const res = await authFetch(
    `${API_BASE}/sessions/${encodeURIComponent(sessionId)}`,
    token,
  )
  if (!res.ok) throw new Error("Failed to fetch session")
  return res.json()
}

export async function upsertSession(
  session: { id: string; title: string; messages: unknown[] },
  token: string | undefined,
): Promise<SessionSummary> {
  const res = await authFetch(`${API_BASE}/sessions/${encodeURIComponent(session.id)}`, token, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(session),
  })
  if (!res.ok) throw new Error("Failed to save session")
  return res.json()
}

export async function deleteSession(
  sessionId: string,
  token: string | undefined,
): Promise<void> {
  const res = await authFetch(
    `${API_BASE}/sessions/${encodeURIComponent(sessionId)}`,
    token,
    { method: "DELETE" },
  )
  if (!res.ok) throw new Error("Failed to delete session")
}
