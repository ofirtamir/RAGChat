"use client"

import type { EvalRun, MetricName, MetricRow, QuestionSource, RunMode } from "@/lib/admin-eval-api"
import { cn } from "@/lib/utils"
import { Pill, TONE } from "./shared"

/** The four headline numbers, in plain Hebrew. `rate` metrics are shares (shown as %). */
export const HEADLINE: { id: MetricName; label: string; hint: string; kind: "rate" | "score" | "count" }[] = [
  { id: "recall@10", label: "Recall@10", kind: "rate",
    hint: "בכמה מהשאלות ההחלטה הנכונה הופיעה ב-10 התוצאות הראשונות" },
  { id: "mrr@20", label: "MRR", kind: "score",
    hint: "כמה גבוה ברשימה מופיעה ההחלטה הנכונה: 1 = תמיד ראשונה, 0.5 = בממוצע שנייה" },
  { id: "hit@1", label: "Hit@1", kind: "rate",
    hint: "בכמה מהשאלות התוצאה הראשונה באה מההחלטה הנכונה" },
  { id: "distinct@10", label: "החלטות שונות ב-10", kind: "count",
    hint: "כמה החלטות שונות יש ב-10 התוצאות הראשונות (יותר = פחות חזרות על אותה החלטה)" },
]

export const METRIC_LABEL: Partial<Record<MetricName | "n", string>> = {
  n: "שאלות",
  "recall@5": "Recall@5", "recall@10": "Recall@10", "recall@20": "Recall@20", "mrr@20": "MRR",
  "hit@1": "Hit@1", "precision@10": "דיוק ב-10 (שאלות נושא)", "section_hit@10": "הסעיף הנכון ב-10",
  "distinct@10": "החלטות שונות ב-10",
  "filter_acc@10": "דיוק סינון", "vector_recall@20": "Recall@20 משמעות בלבד",
  "keyword_recall@20": "Recall@20 מילים בלבד", "candidate_recall@20": "Recall@20 לפני דירוג",
}

export const TYPE_LABEL: Record<string, string> = {
  principle: "עיקרון",
  table: "נתון מטבלה",
  block_plot: "גוש/חלקה",
  metadata: "חיפוש לפי פרטים",
  named: "החלטה לפי שם",
  fact: "פרט מהחלטה",
  analysis: "ניתוח החלטה",
  topic: "נושא במאגר",
  real: "שאלה אמיתית",
  manual: "ידנית",
  all: "הכל",
}

export const SOURCE_LABEL: Record<QuestionSource, string> = {
  synthetic: "נוצרה אוטומטית",
  real: "שאלת משתמש",
  manual: "נוספה ידנית",
}

export const MODE_LABEL: Record<RunMode, { label: string; hint: string }> = {
  hybrid: { label: "חיפוש בלבד", hint: "החיפוש על השאלה כפי שנכתבה, בלי מודל שפה — משלמים רק על יצירת וקטור לכל שאלה, כמעט חינם" },
  pipeline: { label: "כמו בצ׳אט", hint: "ניתוח השאלה, חיפוש לכל שאילתה ודירוג מחדש — שתי קריאות Gemini לשאלה, בתשלום" },
}

export const typeLabel = (t: string | null | undefined) => (t ? TYPE_LABEL[t] ?? t : "—")

export function formatMetric(metric: string, value: number | null | undefined): string {
  if (value == null) return "—"
  if (metric === "n") return String(value)
  if (metric === "distinct@10") return value.toFixed(1)
  if (metric === "mrr@20") return value.toFixed(2)
  return `${Math.round(value * 100)}%`
}

/** Signed change; rates in percentage points. Better = green, worse = red, counts neutral. */
export function Delta({ metric, value }: { metric: string; value: number | null | undefined }) {
  if (value == null) return null
  const neutral = metric === "distinct@10" || metric === "n"
  const text = metric === "n" ? `${value > 0 ? "+" : ""}${value}`
    : metric === "distinct@10" ? `${value > 0 ? "+" : ""}${value.toFixed(1)}`
    : metric === "mrr@20" ? `${value > 0 ? "+" : ""}${value.toFixed(2)}`
    : `${value > 0 ? "+" : ""}${(value * 100).toFixed(1)} נק׳`
  return (
    <span dir="ltr" className={cn("tabular-nums",
      Math.abs(value) < 1e-9 || neutral ? "text-muted-foreground"
        : value > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400")}>
      {Math.abs(value) < 1e-9 ? "±0" : text}
    </span>
  )
}

export const RUN_STATUS: Record<EvalRun["status"], { label: string; className: string }> = {
  running: { label: "רצה", className: TONE.amber },
  done: { label: "הסתיימה", className: TONE.emerald },
  failed: { label: "נכשלה", className: TONE.red },
}

export function ModePill({ mode }: { mode: RunMode }) {
  return <Pill className={mode === "pipeline" ? TONE.violet : TONE.sky}>{MODE_LABEL[mode].label}</Pill>
}

export const runName = (r: EvalRun) => r.label || (r.mode === "pipeline" ? "ריצה כמו בצ׳אט" : "ריצת חיפוש")

export const allRow = (r: EvalRun | null | undefined): MetricRow | undefined => r?.summary?.all
