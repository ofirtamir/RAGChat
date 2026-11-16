"use client"

import { useState } from "react"
import { AgentInfo, AgentFilters } from "@/lib/api"
import { Input } from "@/components/ui/input"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Check, ChevronDown, Library, SlidersHorizontal, X } from "lucide-react"
import { cn } from "@/lib/utils"

interface AgentPanelProps {
  agents: AgentInfo[]
  selectedAgentId: string | null
  onSelectAgent: (id: string) => void
  filters: AgentFilters
  onFiltersChange: (filters: AgentFilters) => void
  /** The text field, rendered inside the pill. */
  children: React.ReactNode
  /** The send button, rendered at the pill's end. */
  action: React.ReactNode
}

function countActiveFilters(filters: AgentFilters): number {
  return Object.values(filters).filter(v =>
    typeof v === "string" ? v.trim() !== "" : Boolean(v?.from || v?.to),
  ).length
}

const fieldClass =
  "h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm transition-colors focus-visible:border-ring focus-visible:outline-none"

/**
 * The chat composer: one rounded "pill" holding the knowledge-base picker,
 * the text field, the filters button and the send button. The agent's
 * filters open as a small card above the pill.
 */
export function AgentPanel({
  agents,
  selectedAgentId,
  onSelectAgent,
  filters,
  onFiltersChange,
  children,
  action,
}: AgentPanelProps) {
  const [showFilters, setShowFilters] = useState(false)
  const agent = agents.find(a => a.id === selectedAgentId) ?? null
  const activeCount = countActiveFilters(filters)
  const hasFilters = Boolean(agent && agent.filters.length > 0)

  const selectAgent = (id: string) => {
    onSelectAgent(id)
    onFiltersChange({})
    setShowFilters(false)
  }

  const setField = (name: string, value: string | { from?: string; to?: string }) => {
    onFiltersChange({ ...filters, [name]: value })
  }

  const dateVal = (name: string): { from?: string; to?: string } => {
    const v = filters[name]
    return typeof v === "object" && v !== null ? v : {}
  }

  return (
    <div className="w-full" dir="rtl">
      {/* Filters card */}
      {agent && hasFilters && showFilters && (
        <div className="mb-2 rounded-3xl border bg-background p-4 shadow-[0_4px_24px_rgba(0,0,0,0.06)] animate-fadeIn dark:bg-popover">
          <div className="mb-3 flex items-center gap-2">
            <p className="flex-1 truncate text-sm font-medium">סינון · {agent.name}</p>
            {activeCount > 0 && (
              <button
                type="button"
                onClick={() => onFiltersChange({})}
                className="rounded-full px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              >
                ניקוי פילטרים
              </button>
            )}
            <button
              type="button"
              onClick={() => setShowFilters(false)}
              className="flex h-7 w-7 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              aria-label="סגירת הסינון"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {agent.filters.map(field => (
              <div key={field.name} className="min-w-0">
                <label className="mb-1 block text-xs text-muted-foreground">{field.label}</label>
                {field.type === "choice" ? (
                  <select
                    value={(filters[field.name] as string) ?? ""}
                    onChange={e => setField(field.name, e.target.value)}
                    className={fieldClass}
                  >
                    <option value="">הכל</option>
                    {(field.values ?? []).map(v => (
                      <option key={v} value={v}>{v}</option>
                    ))}
                  </select>
                ) : field.type === "date" ? (
                  <div className="flex gap-1.5">
                    <Input
                      type="date"
                      value={dateVal(field.name).from ?? ""}
                      onChange={e => setField(field.name, { ...dateVal(field.name), from: e.target.value })}
                      className="text-sm"
                      title="מתאריך"
                    />
                    <Input
                      type="date"
                      value={dateVal(field.name).to ?? ""}
                      onChange={e => setField(field.name, { ...dateVal(field.name), to: e.target.value })}
                      className="text-sm"
                      title="עד תאריך"
                    />
                  </div>
                ) : (
                  <Input
                    value={(filters[field.name] as string) ?? ""}
                    onChange={e => setField(field.name, e.target.value)}
                    placeholder={field.label}
                    className="text-sm"
                  />
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* The pill */}
      <div
        className={cn(
          "flex items-end gap-1 rounded-[28px] border bg-background p-2.5",
          "shadow-[0_2px_16px_rgba(0,0,0,0.06)] transition-shadow focus-within:shadow-[0_2px_20px_rgba(0,0,0,0.1)]",
          "dark:border-transparent dark:bg-muted dark:shadow-none",
        )}
      >
        {agents.length > 0 && (
          <DropdownMenu dir="rtl">
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                className={cn(
                  "flex h-9 shrink-0 items-center gap-1.5 rounded-full px-2.5 text-sm transition-colors hover:bg-accent data-[state=open]:bg-accent",
                  agent ? "text-foreground" : "text-muted-foreground",
                )}
                aria-label="בחירת מאגר מסמכים"
                title={agent?.name}
              >
                <Library className="h-[18px] w-[18px] shrink-0" />
                <span className="hidden max-w-[11rem] truncate sm:inline">{agent?.name ?? "בחירת מאגר"}</span>
                <ChevronDown className="hidden h-3.5 w-3.5 shrink-0 opacity-60 sm:block" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent side="top" align="start" className="w-72 text-right">
              <DropdownMenuLabel>חיפוש במאגר</DropdownMenuLabel>
              {agents.map(a => (
                <DropdownMenuItem key={a.id} className="cursor-pointer" onSelect={() => selectAgent(a.id)}>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">{a.name}</span>
                    <span className="block text-xs text-muted-foreground">{a.chunk_count.toLocaleString()} קטעים</span>
                  </span>
                  {a.id === selectedAgentId && <Check />}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        )}

        <div className="min-w-0 flex-1">{children}</div>

        {hasFilters && (
          <button
            type="button"
            onClick={() => setShowFilters(s => !s)}
            className={cn(
              "relative flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition-colors hover:bg-accent",
              showFilters || activeCount > 0 ? "text-foreground" : "text-muted-foreground",
              showFilters && "bg-accent",
            )}
            aria-label="פילטרים"
            title="פילטרים"
          >
            <SlidersHorizontal className="h-[18px] w-[18px]" />
            {activeCount > 0 && (
              <span className="absolute -start-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-foreground px-1 text-[10px] font-medium text-background">
                {activeCount}
              </span>
            )}
          </button>
        )}

        {action}
      </div>

      {/* Active knowledge base on phones (the chip shows only its icon there) */}
      {agent && (
        <p className="mt-1.5 truncate px-4 text-xs text-muted-foreground sm:hidden">
          מחפש ב{agent.name}{activeCount > 0 && ` · ${activeCount} פילטרים`}
        </p>
      )}
    </div>
  )
}
