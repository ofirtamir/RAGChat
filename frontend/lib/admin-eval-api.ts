// Client for the retrieval benchmark endpoints (backend/admin_eval_api.py).

import { call } from "@/lib/admin-api"

export type RunMode = "hybrid" | "pipeline"
export type QuestionSource = "synthetic" | "real" | "manual"

/** Metric means per question type ("all" = every question); a metric is missing where undefined. */
export type MetricRow = { n: number; errors: number } & Partial<Record<MetricName, number>>

export type MetricName =
  | "recall@5" | "recall@10" | "recall@20" | "mrr@20" | "hit@1" | "section_hit@10" | "distinct@10"
  | "filter_acc@10" | "vector_recall@20" | "keyword_recall@20" | "candidate_recall@20"

export interface EvalSet {
  id: string
  agent_id: string
  name: string
  kind: QuestionSource
  status: "building" | "ready" | "partial" | "failed"
  info: { cost_usd?: number; estimate_usd?: number; error?: string | null; model?: string; n?: number }
  created_at: string
  questions: number
  labelled: number
}

export interface EvalRun {
  id: string
  agent_id: string
  set_id: string
  set_name: string | null
  label: string | null
  mode: RunMode
  k: number
  status: "running" | "done" | "failed"
  started_at: string
  finished_at: string | null
  n_questions: number
  done: number
  cost_estimate: number
  cost_usd: number
  summary: Record<string, MetricRow> | null
  degraded: number
  error: string | null
}

export interface EvalJob {
  running: boolean
  kind: "build" | "run" | null
  set_id: string | null
  run_id: string | null
  label: string | null
  done: number
  total: number
  spent_usd: number
  started_at: string | null
  finished_at: string | null
  error: string | null
  message: string | null
}

export interface EvalBudget {
  budget_usd: number
  spent_usd: number
  remaining_usd: number
}

export interface Decision {
  record_key: string
  title: string | null
}

export interface RetrievedDecision extends Decision {
  section: string | null
  rank: number
}

export interface EvalQuestion {
  id: string
  set_id: string
  question: string
  type: string
  gold: Decision[]
  gold_section: string | null
  expected_meta: Record<string, string>
  source: QuestionSource
  asked_at: string | null
  created_at: string
  log_id: string | null
  result: {
    found_rank: number | null
    metrics: Partial<Record<MetricName, number>>
    top: RetrievedDecision[]
    error: string | null
    search_queries: string[] | null
  } | null
}

export interface QuestionPage {
  total: number
  page: number
  page_size: number
  questions: EvalQuestion[]
  facets: { types: Record<string, number>; sources: Record<string, number> }
}

export interface QuestionFilter {
  type?: string | null
  source?: QuestionSource | null
  q?: string
  run_id?: string | null
  missed?: boolean
  unlabelled?: boolean
  page?: number
}

export interface BuildEstimate {
  n: number
  sampled: number
  meta_questions: number
  llm_questions: number
  model: string
  input_tokens: number
  output_tokens: number
  cost_usd: number
}

export interface RunEstimate {
  questions: number
  mode: RunMode
  cost_usd: number
}

export interface Flip {
  id: string
  type: string | null
  question: string
  a: number
  b: number
  rank_a: number | null
  rank_b: number | null
}

export interface Comparison {
  deltas: Record<string, Partial<Record<MetricName | "n", { a: number | null; b: number | null; delta: number | null }>>>
  gained: Flip[]
  lost: Flip[]
  gained_total: number
  lost_total: number
  shared: number
  same_set: boolean
  flip_metric: MetricName
  a: EvalRun
  b: EvalRun
}

const BASE = "/admin/eval"
const enc = encodeURIComponent
const post = (body: unknown) => ({ method: "POST", body: JSON.stringify(body) })

const qs = (params: Record<string, string | number | boolean | null | undefined>) =>
  Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== "" && v !== false)
    .map(([k, v]) => `${k}=${enc(String(v))}`)
    .join("&")

export const getEvalOverview = (agentId: string, token: string | undefined) =>
  call<{ sets: EvalSet[]; runs: EvalRun[]; job: EvalJob; budget: EvalBudget }>(`${BASE}/overview?agent_id=${enc(agentId)}`, token)

export const setEvalBudget = (budgetUsd: number, token: string | undefined) =>
  call<EvalBudget>(`${BASE}/budget`, token, { method: "PUT", body: JSON.stringify({ budget_usd: budgetUsd }) })

export const getEvalJob = (token: string | undefined) => call<EvalJob>(`${BASE}/job`, token)

export const cancelEvalJob = (token: string | undefined) =>
  call<{ cancelled: boolean }>(`${BASE}/job/cancel`, token, { method: "POST" })

export const getEvalQuestions = (setId: string, filter: QuestionFilter, token: string | undefined) =>
  call<QuestionPage>(`${BASE}/sets/${enc(setId)}/questions?${qs({ ...filter })}`, token)

export const buildEvalSet = (
  body: { agent_id: string; n: number; name?: string; confirm: boolean }, token: string | undefined,
) => call<{ estimate: BuildEstimate; budget: EvalBudget; set?: EvalSet; job?: EvalJob }>(`${BASE}/sets/build`, token, post(body))

export const importRealQuestions = (agentId: string, days: number, token: string | undefined) =>
  call<{ added: number; found: number; set: EvalSet }>(`${BASE}/sets/import-real`, token,
    post({ agent_id: agentId, days }))

export const addEvalQuestion = (
  setId: string, body: { question: string; type?: string; gold: string[] }, token: string | undefined,
) => call<{ added: number }>(`${BASE}/sets/${enc(setId)}/questions`, token, post(body))

export const deleteEvalSet = (setId: string, token: string | undefined) =>
  call<{ deleted: boolean }>(`${BASE}/sets/${enc(setId)}`, token, { method: "DELETE" })

export const patchEvalQuestion = (
  id: string, patch: { gold?: string[]; gold_section?: string; type?: string }, token: string | undefined,
) => call<EvalQuestion>(`${BASE}/questions/${enc(id)}`, token, { method: "PATCH", body: JSON.stringify(patch) })

export const deleteEvalQuestion = (id: string, token: string | undefined) =>
  call<{ deleted: boolean }>(`${BASE}/questions/${enc(id)}`, token, { method: "DELETE" })

export const getGoldSuggestions = (id: string, token: string | undefined) =>
  call<{ decisions: Decision[] }>(`${BASE}/questions/${enc(id)}/suggestions`, token)

export const lookupDecisions = (agentId: string, q: string, token: string | undefined) =>
  call<{ decisions: (Decision & { sort_date: string | null; status: string })[] }>(
    `${BASE}/lookup?${qs({ agent_id: agentId, q })}`, token)

export const startEvalRun = (
  body: { agent_id: string; set_id: string; mode: RunMode; label: string; confirm: boolean },
  token: string | undefined,
) => call<{ estimate: RunEstimate; budget: EvalBudget; run?: EvalRun; job?: EvalJob }>(`${BASE}/runs`, token, post(body))

export const compareRuns = (a: string, b: string, token: string | undefined) =>
  call<Comparison>(`${BASE}/compare?${qs({ a, b })}`, token)
