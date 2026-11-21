"use client"

import { Sparkles, X } from "lucide-react"
import { AutoFilters, FilterCondition } from "@/types/chat"
import { cn } from "@/lib/utils"

/**
 * The filters the last answer found in its question, shown inside the filter
 * panel's own fields: a blue frame and a badge tell them apart from the
 * user's own choices. They are not kept for the next question (a follow-up
 * question keeps them on the server side); editing a field makes it the
 * user's own choice.
 */

export type AutoKind = "applied" | "preferred" | "dropped"
export interface AutoField { condition: FilterCondition; kind: AutoKind }

/** {field: what the last answer did with it}; the user's own panel choices are left out. */
export function autoByField(filters: AutoFilters | null | undefined): Record<string, AutoField> {
  const out: Record<string, AutoField> = {}
  if (!filters) return out
  const add = (list: FilterCondition[] | undefined, kind: AutoKind) => {
    for (const c of list ?? []) {
      if (c.source !== "manual" && !out[c.field]) out[c.field] = { condition: c, kind }
    }
  }
  add(filters.applied, "applied")
  add(filters.preferred, "preferred")
  add(filters.dropped, "dropped")
  return out
}

export function countApplied(byField: Record<string, AutoField>): number {
  return Object.values(byField).filter(a => a.kind === "applied").length
}

/** 20250101 → "2025-01-01" (what <input type="date"> holds). */
function isoFromInt(day: number | null | undefined): string | undefined {
  if (!day) return undefined
  const s = String(day)
  return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`
}

/** The value a panel field shows for a found filter. */
export function autoValue(a: AutoField): string | { from?: string; to?: string } {
  const c = a.condition
  if (c.kind === "range") return { from: isoFromInt(c.start), to: isoFromInt(c.end) }
  if (c.kind === "num") return c.values.join(", ")
  return c.display
}

/** The frame a field gets while it shows a found filter. */
export function autoFieldClass(a: AutoField | undefined): string {
  if (!a) return ""
  return cn(
    "border-blue-400 bg-blue-50/60 dark:border-blue-400/60 dark:bg-blue-400/10",
    a.kind === "preferred" && "border-dashed",
    a.kind === "dropped" && "line-through opacity-70",
  )
}

const BADGE: Record<AutoKind, string> = {
  applied: "זוהה מהשאלה",
  preferred: "העדפה בלבד",
  dropped: "הוסר: אין תוצאות",
}

/** The badge beside a field's label, with removal for an applied filter. */
export function AutoBadge({ auto, onRemove, busy }: {
  auto: AutoField | undefined
  onRemove?: (field: string) => void
  busy?: boolean
}) {
  if (!auto) return null
  const inherited = auto.condition.source === "inherited"
  const text = inherited && auto.kind === "applied" ? "מהשאלה הקודמת" : BADGE[auto.kind]
  return (
    <span
      className="inline-flex items-center gap-0.5 rounded-full bg-blue-50 px-1.5 py-px text-[10px] font-medium text-blue-700 dark:bg-blue-400/10 dark:text-blue-300"
      title={auto.condition.matched ? `מתוך השאלה: ${auto.condition.matched}` : undefined}
    >
      <Sparkles className="h-2.5 w-2.5" aria-hidden />
      {text}
      {auto.kind === "applied" && onRemove && (
        <button
          type="button"
          disabled={busy}
          onClick={() => onRemove(auto.condition.field)}
          aria-label={`הסר את המסנן ${auto.condition.label} ושאל שוב`}
          title="הסר את המסנן ושאל שוב"
          className="-me-0.5 rounded-full p-px transition-colors hover:bg-blue-100 disabled:opacity-50 dark:hover:bg-blue-400/20"
        >
          <X className="h-2.5 w-2.5" />
        </button>
      )}
    </span>
  )
}

/** One line at the top of the panel: the last question was filtered automatically. */
export function AutoSummary({ filters, byField }: {
  filters: AutoFilters | null | undefined
  byField: Record<string, AutoField>
}) {
  const fields = Object.values(byField)
  if (!filters || fields.length === 0) return null
  const applied = countApplied(byField)
  const head = applied
    ? `השאלה האחרונה סוננה אוטומטית לפי ${applied === 1 ? "שדה אחד" : `${applied} שדות`}`
    : "בשאלה האחרונה זוהו ערכים, אבל הם לא סיננו"
  const count = filters.count != null && applied ? ` · ${filters.count.toLocaleString()} החלטות` : ""
  return (
    <div className="mb-3 shrink-0 rounded-xl border border-blue-200 bg-blue-50/60 px-3 py-2 text-xs text-blue-900 dark:border-blue-400/20 dark:bg-blue-400/10 dark:text-blue-100">
      <p className="flex items-center gap-1.5 font-medium">
        <Sparkles className="h-3.5 w-3.5 shrink-0" aria-hidden />
        {head}{count}
      </p>
      {filters.note && <p className="mt-0.5 text-blue-800/80 dark:text-blue-200/80">{filters.note}</p>}
      <p className="mt-0.5 text-blue-800/70 dark:text-blue-200/70">
        השדות המסומנים לא נשמרים לשאלה הבאה. שינוי של שדה הופך אותו לבחירה שלך.
      </p>
    </div>
  )
}
