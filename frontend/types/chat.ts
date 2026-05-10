export interface Message {
  id: string
  role: "user" | "assistant"
  content: string
  sources?: string[]
  route?: RouteInfo | null
  rewritten?: RewrittenQuery | null
  fullDocDecision?: FullDocDecision | null
  plan?: QueryPlan | null
  thinkingSteps?: ThinkingStep[]
  timestamp: Date
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

export interface DocumentInfo {
  source: string
  chunks: number
  chars: number
}

export interface ThinkingStep {
  node: string
  label: string
  icon: string
  detail: string
  status: "running" | "done"
}
