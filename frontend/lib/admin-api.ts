// Client for the admin console endpoints (/api/admin/*) and /api/me.
// Every call carries the Google ID token; the backend decides who is an admin.

import type { AgentFilterField, AgentFilters } from "@/lib/api"

const BACKEND_ORIGIN =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_BACKEND_ORIGIN) ||
  "http://localhost:8000"

const API_BASE = `${BACKEND_ORIGIN}/api`

async function call<T>(path: string, token: string | undefined, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { ...(init?.headers as Record<string, string>) }
  if (token) headers["Authorization"] = `Bearer ${token}`
  if (init?.body) headers["Content-Type"] = "application/json"
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
    throw new Error(typeof err.detail === "string" ? err.detail : `HTTP ${res.status}`)
  }
  return res.json()
}

// ── me ───────────────────────────────────────────────────────────────────

export interface Me {
  email: string
  status: UserStatus
  is_admin: boolean
  allowed: boolean
  reason: "pending" | "blocked" | null
  daily_limit: number
  questions_today: number
}

export const getMe = (token: string | undefined) => call<Me>("/me", token)

// ── settings / usage ─────────────────────────────────────────────────────

export interface AdminSettings {
  require_approval: boolean
  default_daily_limit: number
  monthly_budget_usd: number
  price_input_per_m: number
  price_output_per_m: number
  price_embedding_per_m: number
}

export interface UsageDay {
  day: string
  questions: number
  active_users: number
  input_tokens: number
  output_tokens: number
  chat_cost: number
  indexing_cost: number
}

export interface UsageSummary {
  questions_today: number
  cost_today: number
  questions_month: number
  cost_month: number
  indexing_cost_month: number
  avg_cost_per_question: number
  daily: UsageDay[]
  top_users?: Pick<AdminUser, "sub" | "email" | "name" | "questions_today" | "questions_month" | "cost_month">[]
}

export const getSettings = (token: string | undefined) => call<AdminSettings>("/admin/settings", token)

export const saveSettings = (changes: Partial<AdminSettings>, token: string | undefined) =>
  call<AdminSettings>("/admin/settings", token, { method: "PUT", body: JSON.stringify(changes) })

export const getUsage = (days: number, token: string | undefined) =>
  call<UsageSummary>(`/admin/usage?days=${days}`, token)

// ── users ────────────────────────────────────────────────────────────────

export type UserStatus = "pending" | "approved" | "blocked"

export interface AdminUser {
  sub: string
  email: string
  name: string | null
  picture: string | null
  status: UserStatus
  daily_limit: number | null
  note: string | null
  created_at: string
  last_seen_at: string
  questions_today: number
  questions_month: number
  questions_total: number
  cost_month: number
}

export const getUsers = (token: string | undefined) =>
  call<{ users: AdminUser[]; settings: AdminSettings }>("/admin/users", token)

export const patchUser = (
  sub: string,
  patch: { status?: UserStatus; daily_limit?: number; clear_daily_limit?: boolean; note?: string },
  token: string | undefined,
) => call<AdminUser>(`/admin/users/${encodeURIComponent(sub)}`, token, {
  method: "PATCH",
  body: JSON.stringify(patch),
})

// ── document library ─────────────────────────────────────────────────────

export type DocStatus =
  | "catalog" | "queued" | "processing" | "parsed" | "indexing" | "indexed" | "no_text" | "failed"

export interface IndexCost {
  total_usd: number
  tokens: number
  documents: number
  avg_per_document_usd: number
  projected_catalog_usd: number
}

export interface LibraryCounts {
  counts: Record<DocStatus, number>
  catalog_total: number
  indexed_chunks: number
  flagged_tables: number
  index_cost: IndexCost
}

export interface WorkerStatus {
  last_seen: string | null
  online: boolean
}

export interface LibraryAgent extends LibraryCounts {
  id: string
  name: string
  filters: AgentFilterField[]
}

export interface SyncStatus {
  running: boolean
  agent_id: string | null
  added: number
  checked: number
  error: string | null
}

export interface LibraryDoc {
  agent_id: string
  record_key: string
  title: string
  meta: Record<string, string>
  sort_date: string | null
  status: DocStatus
  auto_index: boolean
  pages: number | null
  tables: number | null
  flagged_tables: number | null
  chars: number | null
  chunks: number | null
  embed_tokens: number | null
  index_cost_usd: number | null
  error: string | null
  updated_at: string
  parsed_at: string | null
  indexed_at: string | null
}

export interface LibraryDocDetail extends LibraryDoc {
  files: { url: string; name: string; ext: string }[]
  problems: { page: number; problems: string[] }[]
  parsed_pages: { number: number; markdown: string }[]
}

export interface LibrarySelection {
  status?: DocStatus | null
  q?: string
  filters?: AgentFilters
  limit?: number
}

export interface LibrarySearchResult extends LibraryCounts {
  total: number
  page: number
  page_size: number
  documents: LibraryDoc[]
  worker: WorkerStatus
}

export type LibraryActionName = "queue" | "queue_and_index" | "cancel" | "index" | "unindex"

export const getLibraryAgents = (token: string | undefined) =>
  call<{ agents: LibraryAgent[]; worker: WorkerStatus; sync: SyncStatus }>("/admin/library/agents", token)

export const searchLibrary = (
  agentId: string,
  selection: LibrarySelection,
  page: number,
  token: string | undefined,
) => call<LibrarySearchResult>("/admin/library/search", token, {
  method: "POST",
  body: JSON.stringify({ agent_id: agentId, ...selection, page, page_size: 50 }),
})

export const getLibraryDoc = (agentId: string, key: string, token: string | undefined) =>
  call<LibraryDocDetail>(`/admin/library/${encodeURIComponent(agentId)}/${encodeURIComponent(key)}`, token)

export const runLibraryAction = (
  agentId: string,
  action: LibraryActionName,
  target: { keys: string[] } | { selection: LibrarySelection },
  token: string | undefined,
) => call<{ moved: number } & LibraryCounts>("/admin/library/action", token, {
  method: "POST",
  body: JSON.stringify({ agent_id: agentId, action, ...target }),
})

export const syncCatalog = (agentId: string, token: string | undefined) =>
  call<{ started: boolean }>(`/admin/library/${encodeURIComponent(agentId)}/sync`, token, { method: "POST" })

// ── overview ─────────────────────────────────────────────────────────────

export interface Overview {
  users: { total: number; pending: number; approved: number; blocked: number; active_today: number }
  usage: UsageSummary
  settings: AdminSettings
  libraries: ({ agent_id: string; name: string } & LibraryCounts)[]
  worker: WorkerStatus
}

export const getOverview = (token: string | undefined) => call<Overview>("/admin/overview", token)
