"use client"

import { cn } from "@/lib/utils"
import type { DocStatus, UsageDay, UserStatus } from "@/lib/admin-api"

/** Soft colour tones shared by every status pill in the console. */
export const TONE = {
  neutral: "bg-muted text-muted-foreground",
  amber:   "bg-amber-50 text-amber-700 dark:bg-amber-400/10 dark:text-amber-300",
  sky:     "bg-sky-50 text-sky-700 dark:bg-sky-400/10 dark:text-sky-300",
  violet:  "bg-violet-50 text-violet-700 dark:bg-violet-400/10 dark:text-violet-300",
  emerald: "bg-emerald-50 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300",
  orange:  "bg-orange-50 text-orange-700 dark:bg-orange-400/10 dark:text-orange-300",
  red:     "bg-red-50 text-red-700 dark:bg-red-400/10 dark:text-red-300",
} as const

export const DOC_STATUS: Record<DocStatus, { label: string; className: string }> = {
  catalog:    { label: "בקטלוג",        className: TONE.neutral },
  queued:     { label: "בתור",          className: TONE.amber },
  processing: { label: "בהורדה ופרסור", className: TONE.amber },
  parsed:     { label: "פורסר",         className: TONE.sky },
  indexing:   { label: "באינדוקס",      className: TONE.violet },
  indexed:    { label: "באינדקס",       className: TONE.emerald },
  no_text:    { label: "סרוק, ללא טקסט", className: TONE.orange },
  failed:     { label: "נכשל",          className: TONE.red },
}

export const DOC_STATUS_ORDER: DocStatus[] = [
  "catalog", "queued", "processing", "parsed", "indexing", "indexed", "no_text", "failed",
]

export const USER_STATUS: Record<UserStatus, { label: string; className: string }> = {
  pending:  { label: "ממתין לאישור", className: TONE.amber },
  approved: { label: "מאושר",        className: TONE.emerald },
  blocked:  { label: "חסום",         className: TONE.red },
}

export function Pill({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium", className)}>
      {children}
    </span>
  )
}

/** Quiet white card used across the console. */
export const CARD = "rounded-2xl border bg-card"

export function StatCard({ label, value, hint, tone }: {
  label: string
  value: React.ReactNode
  hint?: React.ReactNode
  tone?: "warn" | "ok"
}) {
  return (
    <div className={cn(CARD, "p-5")}>
      <p className="text-[13px] text-muted-foreground">{label}</p>
      <p className={cn(
        "mt-1.5 text-[26px] font-semibold leading-tight tracking-tight tabular-nums",
        tone === "warn" && "text-amber-600 dark:text-amber-400",
        tone === "ok" && "text-emerald-600 dark:text-emerald-400",
      )}>
        {value}
      </p>
      {hint && <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{hint}</p>}
    </div>
  )
}

/** Indexing cost so far, per document, and what the whole catalog would cost at that rate. */
export function IndexCostSummary({ cost, catalogTotal, indexed, parse }: {
  cost: import("@/lib/admin-api").IndexCost
  catalogTotal: number
  indexed: number
  parse?: { total_usd: number; llm_tables: number }
}) {
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <StatCard label="עלות ממוצעת לאינדוקס מסמך" value={usdPrecise(cost.documents ? cost.avg_per_document_usd : null)}
        hint={cost.documents ? `לפי ${num(cost.documents)} מסמכים שאונדקסו` : "יוצג אחרי המסמך הראשון"} />
      <StatCard label="עלות האינדוקס עד עכשיו" value={usdPrecise(cost.total_usd)}
        hint={`${num(cost.tokens)} טוקנים · ${num(indexed)} מסמכים`} />
      <StatCard label="צפי לאינדוקס כל הקטלוג" value={cost.documents ? usdPrecise(cost.projected_catalog_usd) : "—"}
        hint={`${num(catalogTotal)} מסמכים, לפי הממוצע`} />
      <StatCard label="עלות הפרסור" value={usdPrecise(parse?.total_usd ?? 0)}
        hint={parse?.llm_tables ? `במחשב שלך; ${num(parse.llm_tables)} טבלאות קשות ותמונות נקראו ב-Gemini` : "רץ במחשב שלך, בחינם"} />
    </div>
  )
}

export function ErrorNote({ message }: { message: string }) {
  if (!message) return null
  return (
    <p className="rounded-xl border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700 dark:border-red-400/20 dark:bg-red-400/10 dark:text-red-300">
      {message}
    </p>
  )
}

export const usd = (n: number) =>
  n >= 100 ? `$${n.toFixed(0)}` : n >= 1 ? `$${n.toFixed(2)}` : n > 0 ? `$${n.toFixed(3)}` : "$0"

/** Small amounts need more digits: one document costs fractions of a cent. */
export const usdPrecise = (n: number | null | undefined) =>
  n == null ? "—" : n >= 1 ? `$${n.toFixed(2)}` : n >= 0.01 ? `$${n.toFixed(3)}` : `$${n.toFixed(4)}`

export const num = (n: number | null | undefined) => (n ?? 0).toLocaleString("he-IL")

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "—"
  const d = new Date(iso)
  return isNaN(d.getTime()) ? iso.slice(0, 10) : d.toLocaleDateString("he-IL")
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "אף פעם"
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (minutes < 1) return "עכשיו"
  if (minutes < 60) return `לפני ${minutes} דק׳`
  if (minutes < 60 * 24) return `לפני ${Math.round(minutes / 60)} שע׳`
  return `לפני ${Math.round(minutes / 60 / 24)} ימים`
}

/** Cost so far this month, stretched to the full month at the same daily pace. */
export function monthProjection(costMonth: number): number {
  const now = new Date()
  const daysInMonth = new Date(now.getUTCFullYear(), now.getUTCMonth() + 1, 0).getDate()
  return (costMonth / Math.max(1, now.getUTCDate())) * daysInMonth
}

/** Plain bar chart: one bar per day, value read by `pick`. */
export function DailyBars({ days, pick, format, barClassName }: {
  days: UsageDay[]
  pick: (d: UsageDay) => number
  format: (n: number) => string
  barClassName?: string
}) {
  const max = Math.max(...days.map(pick), 0)
  return (
    <div dir="ltr" className="flex h-36 items-end gap-[3px]">
      {days.map(d => {
        const value = pick(d)
        return (
          <div
            key={d.day}
            title={`${d.day.slice(8)}/${d.day.slice(5, 7)}: ${format(value)}`}
            className="flex h-full flex-1 items-end"
          >
            <div
              className={cn("w-full rounded-t-[3px] bg-foreground/70 transition-colors hover:bg-foreground", barClassName)}
              style={{ height: max > 0 ? `${Math.max(value > 0 ? 3 : 0, (value / max) * 100)}%` : 0 }}
            />
          </div>
        )
      })}
    </div>
  )
}
