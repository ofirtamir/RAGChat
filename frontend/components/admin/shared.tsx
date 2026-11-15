"use client"

import { cn } from "@/lib/utils"
import type { DocStatus, UsageDay, UserStatus } from "@/lib/admin-api"

export const DOC_STATUS: Record<DocStatus, { label: string; className: string }> = {
  catalog:    { label: "בקטלוג",        className: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300" },
  queued:     { label: "בתור",          className: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300" },
  processing: { label: "בהורדה ופרסור", className: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300" },
  parsed:     { label: "פורסר",         className: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300" },
  indexing:   { label: "באינדוקס",      className: "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300" },
  indexed:    { label: "באינדקס",       className: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300" },
  no_text:    { label: "סרוק, ללא טקסט", className: "bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-300" },
  failed:     { label: "נכשל",          className: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300" },
}

export const DOC_STATUS_ORDER: DocStatus[] = [
  "catalog", "queued", "processing", "parsed", "indexing", "indexed", "no_text", "failed",
]

export const USER_STATUS: Record<UserStatus, { label: string; className: string }> = {
  pending:  { label: "ממתין לאישור", className: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300" },
  approved: { label: "מאושר",        className: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300" },
  blocked:  { label: "חסום",         className: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300" },
}

export function Pill({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap", className)}>
      {children}
    </span>
  )
}

export function StatCard({ label, value, hint, tone }: {
  label: string
  value: React.ReactNode
  hint?: React.ReactNode
  tone?: "warn" | "ok"
}) {
  return (
    <div className="rounded-xl border bg-card p-4">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={cn(
        "mt-1 text-2xl font-semibold tabular-nums",
        tone === "warn" && "text-amber-600 dark:text-amber-400",
        tone === "ok" && "text-emerald-600 dark:text-emerald-400",
      )}>
        {value}
      </p>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

export function ErrorNote({ message }: { message: string }) {
  if (!message) return null
  return (
    <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
      {message}
    </p>
  )
}

export const usd = (n: number) =>
  n >= 100 ? `$${n.toFixed(0)}` : n >= 1 ? `$${n.toFixed(2)}` : n > 0 ? `$${n.toFixed(3)}` : "$0"

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
    <div dir="ltr" className="flex h-32 items-end gap-[3px]">
      {days.map(d => {
        const value = pick(d)
        return (
          <div
            key={d.day}
            title={`${d.day.slice(8)}/${d.day.slice(5, 7)}: ${format(value)}`}
            className="flex h-full flex-1 items-end"
          >
            <div
              className={cn("w-full rounded-t bg-sky-500/80 hover:bg-sky-500", barClassName)}
              style={{ height: max > 0 ? `${Math.max(value > 0 ? 3 : 0, (value / max) * 100)}%` : 0 }}
            />
          </div>
        )
      })}
    </div>
  )
}
