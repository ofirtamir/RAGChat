"use client"

import { useEffect, useRef, useState } from "react"
import { AgentInfo, AgentFilters, startAgentIngest, getIngestStatus, IngestStatus } from "@/lib/api"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Bot, SlidersHorizontal, X, DownloadCloud, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"

interface AgentPanelProps {
  agents: AgentInfo[]
  selectedAgentId: string | null
  onSelectAgent: (id: string | null) => void
  filters: AgentFilters
  onFiltersChange: (filters: AgentFilters) => void
  authToken?: string
  onAgentsChanged?: () => void
}

function countActiveFilters(filters: AgentFilters): number {
  return Object.values(filters).filter(v =>
    typeof v === "string" ? v.trim() !== "" : Boolean(v?.from || v?.to),
  ).length
}

export function AgentPanel({
  agents,
  selectedAgentId,
  onSelectAgent,
  filters,
  onFiltersChange,
  authToken,
  onAgentsChanged,
}: AgentPanelProps) {
  const [showFilters, setShowFilters] = useState(false)
  const [ingest, setIngest] = useState<IngestStatus | null>(null)
  const [ingestError, setIngestError] = useState("")
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const agent = agents.find(a => a.id === selectedAgentId) ?? null
  const activeCount = countActiveFilters(filters)

  // Poll ingest status while a job runs
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current) }, [])

  const handleIngest = async () => {
    if (!agent || !authToken) return
    setIngestError("")
    try {
      await startAgentIngest(agent.id, 100, authToken)
    } catch (e) {
      setIngestError(e instanceof Error ? e.message : "שגיאה")
      return
    }
    setIngest({ running: true, agent_id: agent.id, total: 0, done: 0, ingested: 0, skipped: 0, failed: 0, error: null })
    pollRef.current = setInterval(async () => {
      try {
        const s = await getIngestStatus(authToken)
        setIngest(s)
        if (!s.running) {
          if (pollRef.current) clearInterval(pollRef.current)
          pollRef.current = null
          onAgentsChanged?.()
        }
      } catch {}
    }, 2500)
  }

  if (agents.length === 0) return null

  const setField = (name: string, value: string | { from?: string; to?: string }) => {
    onFiltersChange({ ...filters, [name]: value })
  }

  const dateVal = (name: string): { from?: string; to?: string } => {
    const v = filters[name]
    return typeof v === "object" && v !== null ? v : {}
  }

  return (
    <div className="max-w-3xl mx-auto mb-2" dir="rtl">
      {/* Agent selector row */}
      <div className="flex items-center gap-2">
        <Bot className="w-4 h-4 text-sky-500 shrink-0" />
        <select
          value={selectedAgentId ?? ""}
          onChange={e => {
            onSelectAgent(e.target.value || null)
            onFiltersChange({})
            setShowFilters(false)
          }}
          className="h-9 rounded-lg border border-border bg-card px-2 text-sm min-w-0 flex-1 sm:flex-none sm:w-80"
        >
          <option value="">📁 המסמכים שהעליתי (ללא סוכן)</option>
          {agents.map(a => (
            <option key={a.id} value={a.id}>
              🤖 {a.name} ({a.chunk_count.toLocaleString()} קטעים)
            </option>
          ))}
        </select>

        {agent && agent.filters.length > 0 && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setShowFilters(s => !s)}
            className={cn("h-9 gap-1.5 rounded-lg", activeCount > 0 && "border-sky-400 text-sky-600")}
          >
            <SlidersHorizontal className="w-3.5 h-3.5" />
            פילטרים
            {activeCount > 0 && (
              <span className="rounded-full bg-sky-500 text-white text-[10px] w-4 h-4 flex items-center justify-center">
                {activeCount}
              </span>
            )}
          </Button>
        )}

        {activeCount > 0 && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onFiltersChange({})}
            className="h-9 px-2 text-muted-foreground"
            title="ניקוי פילטרים"
          >
            <X className="w-3.5 h-3.5" />
          </Button>
        )}

        {agent && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleIngest}
            disabled={Boolean(ingest?.running)}
            className="h-9 gap-1.5 rounded-lg"
            title="משיכת 100 המסמכים האחרונים מהאתר אל מאגר הסוכן"
          >
            {ingest?.running ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <DownloadCloud className="w-3.5 h-3.5" />
            )}
            עדכון מאגר
          </Button>
        )}
      </div>

      {/* Ingest progress / result */}
      {agent && (ingest || ingestError) && (
        <div className="mt-1.5 text-xs text-muted-foreground" dir="rtl">
          {ingestError ? (
            <span className="text-red-500">שגיאה: {ingestError}</span>
          ) : ingest?.running ? (
            <span>
              מושך מסמכים מהאתר... {ingest.done}/{ingest.total || "?"}
              {ingest.ingested > 0 && ` · נוספו ${ingest.ingested}`}
              {ingest.skipped > 0 && ` · דולגו ${ingest.skipped} (קיימים/סרוקים)`}
            </span>
          ) : ingest ? (
            <span className="text-emerald-600">
              ✓ העדכון הסתיים — נוספו {ingest.ingested}, דולגו {ingest.skipped}
              {ingest.failed > 0 && `, נכשלו ${ingest.failed}`}
              {ingest.error && ` · שגיאה: ${ingest.error}`}
            </span>
          ) : null}
        </div>
      )}

      {/* Filters panel */}
      {agent && showFilters && (
        <div className="mt-2 rounded-xl border border-border bg-card p-3 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {agent.filters.map(field => (
            <div key={field.name} className="min-w-0">
              <label className="block text-xs text-muted-foreground mb-1">{field.label}</label>
              {field.type === "choice" ? (
                <select
                  value={(filters[field.name] as string) ?? ""}
                  onChange={e => setField(field.name, e.target.value)}
                  className="h-9 w-full rounded-lg border border-border bg-background px-2 text-sm"
                >
                  <option value="">— הכל —</option>
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
                    className="h-9 text-sm"
                    title="מתאריך"
                  />
                  <Input
                    type="date"
                    value={dateVal(field.name).to ?? ""}
                    onChange={e => setField(field.name, { ...dateVal(field.name), to: e.target.value })}
                    className="h-9 text-sm"
                    title="עד תאריך"
                  />
                </div>
              ) : (
                <Input
                  value={(filters[field.name] as string) ?? ""}
                  onChange={e => setField(field.name, e.target.value)}
                  placeholder={field.label}
                  className="h-9 text-sm"
                />
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
