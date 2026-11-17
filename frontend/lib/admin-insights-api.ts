// Client for the admin quality and insight endpoints (backend/admin_insights_api.py).

import { authFetch } from "@/lib/api"
import type { AgentFilters } from "@/lib/api"
import { API_BASE, call } from "@/lib/admin-api"
import type { AdminUser } from "@/lib/admin-api"

// ── chunks ───────────────────────────────────────────────────────────────

export type ChunkFlag = "tiny" | "long" | "no_section" | "garbled" | "duplicate" | "table"

export interface ChunkRow {
  text: string
  section: string
  page: number
  chunk_index: number
  chars: number
  tokens_est: number
  flags: ChunkFlag[]
}

export interface ChunkSummary {
  chunks: number
  chars: number
  tokens_est: number
  min: number
  median: number
  max: number
  sections: number
  flags: Partial<Record<ChunkFlag, number>>
  with_problems: number
  size_buckets: number[]
}

export interface DocumentChunks {
  source: "index" | "preview" | "none"
  chunks: ChunkRow[]
  summary: ChunkSummary | null
}

export const getDocumentChunks = (agentId: string, key: string, preview: boolean, token: string | undefined) =>
  call<DocumentChunks>(
    `/admin/library/${encodeURIComponent(agentId)}/${encodeURIComponent(key)}/chunks${preview ? "?preview=1" : ""}`,
    token)

export interface LibraryChunkQuality {
  documents: number
  measured: number
  chunks: number
  chars: number
  avg_chars: number | null
  with_problems: number
  flags: Partial<Record<ChunkFlag, number>>
  size_buckets: number[]
  worst_documents: ({ record_key: string; title: string } & ChunkSummary)[]
}

export const getChunkQuality = (agentId: string, token: string | undefined) =>
  call<LibraryChunkQuality>(`/admin/chunk-quality/${encodeURIComponent(agentId)}`, token)

// ── search lab ───────────────────────────────────────────────────────────

export interface SearchLabRow {
  id: string
  fused_rank: number
  rrf: number
  in_top_k: boolean
  semantic_rank: number | null
  semantic_similarity: number | null
  keyword_rank: number | null
  keyword_score: number | null
  rerank_score?: number | null
  final_rank?: number | null
  record_key: string
  source: string
  page: number
  section: string
  chunk_index: number
  text: string
}

export interface SearchLabResult {
  rows: SearchLabRow[]
  errors: Record<string, string>
  where: string | null
  keyword_terms: string
  semantic_count: number
  keyword_count: number
  candidates: number
  final_k: number
  reranked: boolean
}

export const runSearchLab = (
  agentId: string, query: string, filters: AgentFilters, rerank: boolean, token: string | undefined,
) => call<SearchLabResult>("/admin/search-lab", token, {
  method: "POST",
  body: JSON.stringify({ agent_id: agentId, query, filters, rerank }),
})

// ── questions and quality ────────────────────────────────────────────────

export type QuestionRoute = "retrieval" | "full_doc" | "chitchat" | "no_docs"
export type QuestionFlag = "down" | "up" | "feedback" | "no_answer" | "error" | "slow" | "no_sources"

export interface QuestionRow {
  id: string
  ts: string
  user_sub?: string
  agent_id: string | null
  query: string
  route: QuestionRoute | null
  retrieved: number
  answer_chars: number
  no_answer: number
  latency_ms: number | null
  first_token_ms: number | null
  cost_usd: number
  ok: number
  error: string | null
  rating: number | null
  comment: string | null
  email: string | null
  name: string | null
}

export interface RetrievedChunk {
  record_key: string | null
  source: string | null
  page: number | null
  section: string | null
  chunk_index: number | null
  score: number | null
  rerank: number | null
}

export interface QuestionDetail extends QuestionRow {
  session_id: string | null
  filters: AgentFilters
  search_queries: string[]
  retrieval: RetrievedChunk[]
  answer: string | null
  input_tokens: number
  output_tokens: number
  llm_calls: number
  models: string | null
  feedback_at: string | null
}

export interface QuestionPage {
  total: number
  page: number
  page_size: number
  questions: QuestionRow[]
}

export interface QuestionQuery {
  days: number
  flag?: QuestionFlag | null
  user?: string
  q?: string
  sort?: "recent" | "cost" | "slow"
  page?: number
}

const qs = (params: Record<string, string | number | null | undefined>) =>
  Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
    .join("&")

export const getQuestions = (query: QuestionQuery, token: string | undefined) =>
  call<QuestionPage>(`/admin/questions?${qs({ ...query })}`, token)

export const getQuestion = (id: string, token: string | undefined) =>
  call<QuestionDetail>(`/admin/questions/${encodeURIComponent(id)}`, token)

export interface QualityDay {
  day: string
  questions: number
  up: number
  down: number
  errors: number
  no_answer: number
}

export interface QualitySummary {
  questions: number
  errors: number
  feedback: number
  up: number
  down: number
  satisfaction: number | null
  no_answer: number
  no_sources: number
  routes: Record<QuestionRoute, number>
  latency_ms: { p50: number | null; p95: number | null }
  first_token_ms: { p50: number | null; p95: number | null }
  slow: number
  top_documents: { record_key: string; title: string; questions: number; down: number }[]
  refusals: { reason: string; n: number; users: number }[]
  daily: QualityDay[]
}

export const getQuality = (days: number, token: string | undefined) =>
  call<QualitySummary>(`/admin/quality?days=${days}`, token)

// ── engagement and costs ─────────────────────────────────────────────────

export type Segment = "power" | "regular" | "dormant" | "never"

export interface SegmentUser {
  sub: string
  email: string
  name: string | null
  questions_30d: number
  last_question: string | null
  created_at: string
}

export interface Engagement {
  dau: number
  wau: number
  mau: number
  stickiness: number | null
  approved_users: number
  activation: number | null
  daily_active: { day: string; users: number }[]
  signups: { week: string; signed_up: number; activated: number }[]
  cohorts: { week: string; size: number; retention: number[] }[]
  segments: Record<Segment, { count: number; users: SegmentUser[] }>
}

export const getEngagement = (token: string | undefined) => call<Engagement>("/admin/engagement", token)

export interface Costs {
  days: number
  by_kind: { kind: string; events: number; cost: number; input_tokens: number; output_tokens: number }[]
  by_model: { model: string; kind: string; events: number; cost: number }[]
  by_route: { route: string; questions: number; avg_cost: number; avg_input: number; avg_output: number }[]
  per_question: {
    count: number; avg: number | null; p50: number | null; p90: number | null; p99: number | null
    max: number | null; avg_input_tokens: number | null; avg_output_tokens: number | null
    avg_llm_calls: number | null
  }
  unit: {
    active_users: number
    approved_users: number
    active_share: number | null
    questions_per_active_user_month: number | null
    chat_cost_per_active_user_month: number | null
    user_cost_p90_month: number | null
  }
  expensive_questions: {
    id: string; ts: string; query: string; route: string | null; retrieved: number; cost_usd: number
    input_tokens: number; output_tokens: number; llm_calls: number; email: string | null
  }[]
}

export const getCosts = (days: number, token: string | undefined) =>
  call<Costs>(`/admin/costs?days=${days}`, token)

// ── users ────────────────────────────────────────────────────────────────

export interface UserActivity {
  user: AdminUser
  questions: number
  active_days: number
  first_ts: string | null
  last_ts: string | null
  up: number
  down: number
  no_answer: number
  errors: number
  cost: number
  refused: number
  daily: { day: string; questions: number }[]
  recent: QuestionRow[]
}

export const getUserActivity = (sub: string, token: string | undefined) =>
  call<UserActivity>(`/admin/users/${encodeURIComponent(sub)}/activity`, token)

export const bulkUserStatus = (subs: string[], status: AdminUser["status"], token: string | undefined) =>
  call<{ updated: number }>("/admin/users/bulk", token, {
    method: "POST",
    body: JSON.stringify({ subs, status }),
  })

// ── exports ──────────────────────────────────────────────────────────────

/** Download a CSV export (the request needs the sign-in token, so it can't be a plain link). */
export async function downloadExport(
  name: "users" | "questions",
  params: Record<string, string | number | null | undefined>,
  token: string | undefined,
) {
  const query = qs(params)
  const res = await authFetch(`${API_BASE}/admin/export/${name}.csv${query ? `?${query}` : ""}`, token)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const url = URL.createObjectURL(await res.blob())
  const a = document.createElement("a")
  a.href = url
  a.download = `${name}-${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
