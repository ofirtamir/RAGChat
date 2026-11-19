"use client"

import { X, Filter } from "lucide-react"
import { AutoFilters, FilterCondition } from "@/types/chat"
import { cn } from "@/lib/utils"

/**
 * The filters an answer ran with: those found in the question (removable — the
 * question is asked again without that filter), the user's own panel choices,
 * uncertain values that only raised matching decisions, and found values that
 * matched nothing and were dropped.
 */
export function FilterChips({ filters, onRemove, disabled }: {
  filters: AutoFilters
  onRemove?: (field: string) => void
  disabled?: boolean
}) {
  const applied = filters.applied ?? []
  const preferred = filters.preferred ?? []
  const dropped = filters.dropped ?? []
  if (!applied.length && !preferred.length && !dropped.length) return null

  const chip = (c: FilterCondition, kind: "applied" | "preferred" | "dropped") => (
    <span
      key={`${kind}-${c.field}-${c.display}`}
      title={c.matched ? `מתוך השאלה: ${c.matched}` : undefined}
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs",
        kind === "applied" && "border-border bg-muted text-foreground",
        kind === "preferred" && "border-dashed border-border text-muted-foreground",
        kind === "dropped" && "border-border text-muted-foreground line-through",
      )}
    >
      <span className="text-muted-foreground">{c.label}:</span>
      <span>{c.display}</span>
      {kind === "preferred" && <span className="text-muted-foreground">(העדפה)</span>}
      {c.source === "inherited" && <span className="text-muted-foreground">(מהשאלה הקודמת)</span>}
      {kind === "applied" && c.source !== "manual" && onRemove && (
        <button
          type="button"
          disabled={disabled}
          onClick={() => onRemove(c.field)}
          aria-label={`הסר את המסנן ${c.label}`}
          className="-me-1 rounded-full p-0.5 text-muted-foreground transition-colors hover:bg-background hover:text-foreground disabled:opacity-50"
        >
          <X className="h-3 w-3" />
        </button>
      )}
    </span>
  )

  return (
    <div dir="rtl" className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <Filter className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
        {applied.map(c => chip(c, "applied"))}
        {preferred.map(c => chip(c, "preferred"))}
        {dropped.map(c => chip(c, "dropped"))}
      </div>
      {filters.note && <p className="text-xs text-muted-foreground">{filters.note}</p>}
    </div>
  )
}
