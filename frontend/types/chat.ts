export interface CitationDetail {
  source: string
  snippet: string
  /** Present for library documents — lets the UI open the cited document. */
  agent_id?: string
  record_key?: string
  page?: number | null
}

export interface Message {
  id: string
  role: "user" | "assistant"
  content: string
  sources?: string[]
  citations?: CitationDetail[]
  route?: RouteInfo | null
  rewritten?: RewrittenQuery | null
  fullDocDecision?: FullDocDecision | null
  plan?: QueryPlan | null
  thinkingSteps?: ThinkingStep[]
  /** Server id of the answered question; lets the user rate the answer. */
  questionId?: string | null
  /** The user's rating: 1 helpful, -1 not helpful. */
  feedback?: { rating: 1 | -1; comment?: string } | null
  /** Filters the server found in the question (or kept from the previous answer). */
  autoFilters?: AutoFilters | null
  /** Found-filter fields the user removed before this answer was produced. */
  disabledFilters?: string[]
  timestamp: Date
}

/** One filter condition, as src/filter_conditions.Condition.to_dict sends it. */
export interface FilterCondition {
  field: string
  kind: "in" | "num" | "range" | "keys"
  values: string[]
  start?: number | null
  end?: number | null
  label: string
  display: string
  matched: string
  source: "auto" | "manual" | "inherited"
  certain: boolean
}

export interface AutoFilters {
  applied: FilterCondition[]
  /** Uncertain values: they only raise matching decisions in the ranking. */
  preferred: FilterCondition[]
  /** Found filters dropped because no decision matched them. */
  dropped: FilterCondition[]
  count: number | null
  mode: "none" | "filtered" | "full_read" | "relaxed"
  note: string
  /** Sent back with the next question; a follow-up keeps these filters. */
  inherit: FilterCondition[]
}

export interface RouteInfo {
  needs_retrieval: boolean
  reasoning: string
}

export interface RewrittenQuery {
  original_query: string
  rewritten_queries: string[]
  reasoning: string
}

export interface FullDocDecision {
  needs_full_document: boolean
  target_sources: string[]
  reasoning: string
}

export interface QueryPlan {
  is_complex: boolean
  sub_queries: string[]
  reasoning: string
}

export interface ChatSession {
  id: string
  title: string
  messages: Message[]
  createdAt: Date
}

export interface ThinkingStep {
  node: string
  label: string
  icon: string
  detail: string
  status: "running" | "done"
}
