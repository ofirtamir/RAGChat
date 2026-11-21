"use client"

import { useEffect, useLayoutEffect, useRef, useState } from "react"
import { AgentInfo, AgentFilters } from "@/lib/api"
import { Input } from "@/components/ui/input"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Check, ChevronDown, Library, SlidersHorizontal, Sparkles, X } from "lucide-react"
import { cn } from "@/lib/utils"
import { FilterMatches } from "@/components/filter-matches"
import { AutoBadge, AutoSummary, autoByField, autoFieldClass, autoValue, countApplied } from "@/components/auto-filters"
import { AutoFilters } from "@/types/chat"

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
  /** The filters the last answer found in its question (shown in the fields, marked). */
  autoFilters?: AutoFilters | null
  /** Ask the last question again without a found filter. */
  onRemoveAutoFilter?: (field: string) => void
  busy?: boolean
}

function isSet(v: AgentFilters[string] | undefined): boolean {
  return typeof v === "string" ? v.trim() !== "" : Boolean(v?.from || v?.to)
}

function countActiveFilters(filters: AgentFilters): number {
  return Object.values(filters).filter(isSet).length
}

const fieldClass =
  "h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm transition-colors focus-visible:border-ring focus-visible:outline-none"

const CARD_TOP_GAP = 72   // px kept free above the filters card (the top bar and a margin)

type DateValue ={ from?: string; to?: string }

/** YYYY-MM-DD in local time (what <input type="date"> holds). */
function isoDay(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`
}

function yearsBack(years: number): DateValue {
  const now = new Date()
  const start = new Date(now)
  start.setFullYear(now.getFullYear() - years)
  return { from: isoDay(start), to: isoDay(now) }
}

const DATE_PRESETS: { label: string; range: () => DateValue }[] = [
  { label: "השנה", range: () => ({ from: `${new Date().getFullYear()}-01-01`, to: isoDay(new Date()) }) },
  { label: "שנה אחרונה", range: () => yearsBack(1) },
  { label: "3 שנים", range: () => yearsBack(3) },
  { label: "5 שנים", range: () => yearsBack(5) },
]

/** A "from – to" date pair with labels and one-click ranges. */
function DateRange({ value, onChange, inputClass }: {
  value: DateValue
  onChange: (v: DateValue) => void
  inputClass?: string
}) {
  const reversed = Boolean(value.from && value.to && value.from > value.to)
  return (
    <div className="space-y-1.5">
      <div className="flex items-end gap-1.5">
        <label className="min-w-0 flex-1">
          <span className="mb-0.5 block text-[11px] text-muted-foreground">מתאריך</span>
          <Input
            type="date"
            value={value.from ?? ""}
            max={value.to || undefined}
            onChange={e => onChange({ ...value, from: e.target.value })}
            className={cn("text-sm", inputClass)}
          />
        </label>
        <span className="pb-2 text-muted-foreground" aria-hidden>–</span>
        <label className="min-w-0 flex-1">
          <span className="mb-0.5 block text-[11px] text-muted-foreground">עד תאריך</span>
          <Input
            type="date"
            value={value.to ?? ""}
            min={value.from || undefined}
            onChange={e => onChange({ ...value, to: e.target.value })}
            className={cn("text-sm", inputClass)}
          />
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-1">
        {DATE_PRESETS.map(p => {
          const range = p.range()
          const active = value.from === range.from && value.to === range.to
          return (
            <button
              key={p.label}
              type="button"
              onClick={() => onChange(active ? {} : range)}
              className={cn(
                "rounded-full border px-2 py-0.5 text-[11px] transition-colors hover:bg-accent",
                active ? "border-foreground bg-foreground text-background hover:bg-foreground/90" : "text-muted-foreground",
              )}
            >
              {p.label}
            </button>
          )
        })}
        {(value.from || value.to) && (
          <button
            type="button"
            onClick={() => onChange({})}
            className="rounded-full px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            ניקוי
          </button>
        )}
      </div>
      {reversed && <p className="text-[11px] text-destructive">תאריך ההתחלה מאוחר מתאריך הסיום</p>}
    </div>
  )
}

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
  autoFilters,
  onRemoveAutoFilter,
  busy,
}: AgentPanelProps) {
  const [showFilters, setShowFilters] = useState(false)
  const [cardMaxHeight, setCardMaxHeight] = useState<number | undefined>(undefined)
  const rootRef = useRef<HTMLDivElement>(null)
  const cardRef = useRef<HTMLDivElement>(null)
  const filterButtonRef = useRef<HTMLButtonElement>(null)

  // The card may use the space between the top of the window and the pill.
  useLayoutEffect(() => {
    if (!showFilters) return
    const fit = () => {
      const top = rootRef.current?.getBoundingClientRect().top ?? window.innerHeight
      setCardMaxHeight(Math.max(200, top - CARD_TOP_GAP))
    }
    fit()
    window.addEventListener("resize", fit)
    return () => window.removeEventListener("resize", fit)
  }, [showFilters])

  // A click outside the card (or Escape) closes it; not while a document opened from it is showing.
  useEffect(() => {
    if (!showFilters) return
    const dialogOpen = () => document.querySelector('[role="dialog"]') !== null
    const onPointerDown = (e: PointerEvent) => {
      const target = e.target as Node
      if (cardRef.current?.contains(target) || filterButtonRef.current?.contains(target) || dialogOpen()) return
      setShowFilters(false)
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !dialogOpen()) setShowFilters(false)
    }
    document.addEventListener("pointerdown", onPointerDown)
    document.addEventListener("keydown", onKeyDown)
    return () => {
      document.removeEventListener("pointerdown", onPointerDown)
      document.removeEventListener("keydown", onKeyDown)
    }
  }, [showFilters])

  const agent = agents.find(a => a.id === selectedAgentId) ?? null
  const activeCount = countActiveFilters(filters)
  const hasFilters = Boolean(agent && agent.filters.length > 0)
  // found filters, except on fields the user has set (their own choice wins)
  const auto = Object.fromEntries(Object.entries(autoByField(autoFilters))
    .filter(([name]) => !isSet(filters[name])))
  const autoCount = countApplied(auto)

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
    <div ref={rootRef} className="relative w-full" dir="rtl">
      {/* Filters card: floats above the pill and scrolls inside, so the pill stays on screen */}
      {agent && hasFilters && showFilters && (
        <div
          ref={cardRef}
          style={{ maxHeight: cardMaxHeight }}
          className="scrollbar-thin absolute inset-x-0 bottom-full z-30 mb-2 flex flex-col overflow-y-auto rounded-3xl border bg-background p-4 shadow-[0_4px_24px_rgba(0,0,0,0.08)] animate-fadeIn dark:bg-popover"
        >
          <div className="mb-3 flex shrink-0 items-center gap-2">
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
          <AutoSummary filters={autoFilters} byField={auto} />
          <div className="grid shrink-0 grid-cols-1 gap-x-3 gap-y-2.5 sm:grid-cols-2 lg:grid-cols-4">
            {agent.filters.map(field => {
              const found = auto[field.name]
              const shown = found ? autoValue(found) : undefined
              const text = typeof shown === "string" ? shown : undefined
              return (
                <div key={field.name} className={cn("min-w-0", field.type === "date" && "sm:col-span-2")}>
                  <div className="mb-1 flex min-w-0 items-center gap-1.5">
                    <label className="truncate text-xs text-muted-foreground">{field.label}</label>
                    <AutoBadge auto={found} onRemove={onRemoveAutoFilter} busy={busy} />
                  </div>
                  {field.type === "choice" ? (
                    <select
                      value={text ?? (filters[field.name] as string) ?? ""}
                      onChange={e => setField(field.name, e.target.value)}
                      className={cn(fieldClass, autoFieldClass(found))}
                    >
                      <option value="">הכל</option>
                      {text && !(field.values ?? []).includes(text) && <option value={text}>{text}</option>}
                      {(field.values ?? []).map(v => (
                        <option key={v} value={v}>{v}</option>
                      ))}
                    </select>
                  ) : field.type === "date" ? (
                    <DateRange
                      value={shown && typeof shown === "object" ? shown : dateVal(field.name)}
                      onChange={v => setField(field.name, v)}
                      inputClass={autoFieldClass(found)}
                    />
                  ) : (
                    <Input
                      value={text ?? (filters[field.name] as string) ?? ""}
                      onChange={e => setField(field.name, e.target.value)}
                      placeholder={field.label}
                      className={cn("text-sm", autoFieldClass(found))}
                    />
                  )}
                </div>
              )
            })}
          </div>
          <FilterMatches agentId={agent.id} filters={filters} />
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
            ref={filterButtonRef}
            type="button"
            onClick={() => setShowFilters(s => !s)}
            className={cn(
              "relative flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition-colors hover:bg-accent",
              showFilters || activeCount > 0 ? "text-foreground" : "text-muted-foreground",
              showFilters && "bg-accent",
            )}
            aria-label="פילטרים"
            title={autoCount > 0 ? `פילטרים · ${autoCount} זוהו מהשאלה האחרונה` : "פילטרים"}
          >
            <SlidersHorizontal className="h-[18px] w-[18px]" />
            {activeCount > 0 && (
              <span className="absolute -start-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-foreground px-1 text-[10px] font-medium text-background">
                {activeCount}
              </span>
            )}
            {autoCount > 0 && (
              <span
                className={cn(
                  "absolute -start-0.5 flex h-4 min-w-4 items-center justify-center gap-px rounded-full bg-blue-600 px-1 text-[10px] font-medium text-white dark:bg-blue-500",
                  activeCount > 0 ? "-bottom-0.5" : "-top-0.5",
                )}
              >
                <Sparkles className="h-2.5 w-2.5" aria-hidden />
                {autoCount}
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
          {autoCount > 0 && ` · ${autoCount} זוהו מהשאלה`}
        </p>
      )}
    </div>
  )
}
