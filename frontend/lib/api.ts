import { ThinkingStep } from "@/types/chat"

// Backend base URL (override via NEXT_PUBLIC_BACKEND_ORIGIN, e.g. http://localhost:8000)
const BACKEND_ORIGIN =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_BACKEND_ORIGIN) ||
  "http://localhost:8000"

const API_BASE = `${BACKEND_ORIGIN}/api`

export interface ChatApiResponse {
  answer: string
  sources: string[]
  citations?: { source: string; snippet: string }[]
  route: { needs_retrieval: boolean; reasoning: string } | null
  rewritten: { original_query: string; rewritten_queries: string[]; reasoning: string } | null
  full_doc_decision: { needs_full_document: boolean; target_sources: string[]; reasoning: string } | null
  plan: { is_complex: boolean; sub_queries: string[]; reasoning: string } | null
}

export interface UserContext {
  id?: string
  email?: string | null
}

export async function sendMessage(
  query: string,
  chatHistory: { role: string; content: string }[],
  sessionId: string,
  user?: UserContext
): Promise<ChatApiResponse> {
  const res = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      chat_history: chatHistory,
      session_id: sessionId,
      user_id: user?.id,
      user_email: user?.email,
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
  user?: UserContext,
): Promise<ChatApiResponse> {
  const res = await fetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      chat_history: chatHistory,
      session_id: sessionId,
      user_id: user?.id,
      user_email: user?.email,
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

export async function uploadFiles(files: File[]): Promise<{ results: { filename: string; status: string; chunks?: number; message?: string }[] }> {
  const formData = new FormData()
  files.forEach(f => formData.append("files", f))
  const res = await fetch(`${API_BASE}/upload`, { method: "POST", body: formData })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Upload failed" }))
    throw new Error(err.detail || "Upload failed")
  }
  return res.json()
}

export async function getDocuments(): Promise<{ total_chunks: number; documents: { source: string; chunks: number; chars: number }[] }> {
  const res = await fetch(`${API_BASE}/documents`)
  if (!res.ok) throw new Error("Failed to fetch documents")
  return res.json()
}

export async function clearDocuments(): Promise<void> {
  const res = await fetch(`${API_BASE}/documents`, { method: "DELETE" })
  if (!res.ok) throw new Error("Failed to clear documents")
}
