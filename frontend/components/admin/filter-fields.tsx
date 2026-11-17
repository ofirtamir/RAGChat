"use client"

import { Input } from "@/components/ui/input"
import type { AgentFilterField, AgentFilters } from "@/lib/api"
import { cn } from "@/lib/utils"

export const selectClass =
  "h-9 rounded-lg border border-input bg-background px-2.5 text-sm transition-colors focus-visible:border-ring focus-visible:outline-none"

type FilterValue = string | { from?: string; to?: string }

/** Set or clear one filter value (empty values are removed). */
export function withFilter(prev: AgentFilters, name: string, value: FilterValue): AgentFilters {
  const next = { ...prev }
  const empty = typeof value === "string" ? !value : !value.from && !value.to
  if (empty) delete next[name]
  else next[name] = value
  return next
}

/** One input per agent filter field: a select for choices, a date range, or free text. */
export function FilterFields({ fields, filters, onChange }: {
  fields: AgentFilterField[]
  filters: AgentFilters
  onChange: (name: string, value: FilterValue) => void
}) {
  return (
    <>
      {fields.filter(f => !f.name.includes("Header")).map(f => {
        if (f.type === "choice") {
          return (
            <label key={f.name} className="text-xs text-muted-foreground">
              {f.label}
              <select
                className={cn(selectClass, "mt-1 block max-w-[11rem]")}
                value={(filters[f.name] as string) ?? ""}
                onChange={e => onChange(f.name, e.target.value)}
              >
                <option value="">הכל</option>
                {(f.values ?? []).map(v => <option key={v} value={v}>{v}</option>)}
              </select>
            </label>
          )
        }
        if (f.type === "date") {
          const value = (filters[f.name] as { from?: string; to?: string }) ?? {}
          return (
            <label key={f.name} className="text-xs text-muted-foreground">
              {f.label}
              <span className="mt-1 flex items-center gap-1">
                <Input type="date" className="h-9 w-[8.5rem]" value={value.from ?? ""}
                  onChange={e => onChange(f.name, { ...value, from: e.target.value })} />
                –
                <Input type="date" className="h-9 w-[8.5rem]" value={value.to ?? ""}
                  onChange={e => onChange(f.name, { ...value, to: e.target.value })} />
              </span>
            </label>
          )
        }
        return (
          <label key={f.name} className="text-xs text-muted-foreground">
            {f.label}
            <Input className="mt-1 h-9 w-24" value={(filters[f.name] as string) ?? ""}
              onChange={e => onChange(f.name, e.target.value)} />
          </label>
        )
      })}
    </>
  )
}
