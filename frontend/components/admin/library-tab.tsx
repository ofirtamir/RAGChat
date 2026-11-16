"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import type { AgentFilters } from "@/lib/api"
import {
  DocStatus, getLibraryAgents, LibraryActionName, LibraryAgent, LibrarySearchResult, LibrarySelection,
  runLibraryAction, searchLibrary, SyncStatus, syncCatalog,
} from "@/lib/admin-api"
import { cn } from "@/lib/utils"
import { DocumentViewer } from "./document-viewer"
import {
  CARD, DOC_STATUS, DOC_STATUS_ORDER, ErrorNote, IndexCostSummary, num, Pill, relativeTime, shortDate,
  TONE, usdPrecise,
} from "./shared"

const ACTIONS: { name: LibraryActionName; label: string; from: DocStatus[]; confirm?: string }[] = [
  { name: "queue", label: "הורד ופרסר", from: ["catalog", "failed", "no_text", "parsed"] },
  { name: "queue_and_index", label: "הורד, פרסר ואנדקס", from: ["catalog", "failed", "no_text", "parsed"] },
  { name: "index", label: "הכנס לאינדקס", from: ["parsed", "indexed"] },
  { name: "unindex", label: "הסר מהאינדקס", from: ["indexed"], confirm: "להסיר מהאינדקס?" },
  { name: "cancel", label: "בטל תור", from: ["queued", "processing"] },
]

const selectClass =
  "h-9 rounded-lg border border-input bg-background px-2.5 text-sm transition-colors focus-visible:border-ring focus-visible:outline-none"

export function LibraryTab({ token }: { token: string | undefined }) {
  const [agents, setAgents] = useState<LibraryAgent[]>([])
  const [agentId, setAgentId] = useState("")
  const [sync, setSync] = useState<SyncStatus | null>(null)
  const [status, setStatus] = useState<DocStatus | null>(null)
  const [q, setQ] = useState("")
  const [filters, setFilters] = useState<AgentFilters>({})
  const [page, setPage] = useState(1)
  const [result, setResult] = useState<LibrarySearchResult | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [bulkLimit, setBulkLimit] = useState(100)
  const [openKey, setOpenKey] = useState<string | null>(null)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [busy, setBusy] = useState(false)

  const agent = agents.find(a => a.id === agentId)
  const selection: LibrarySelection = useMemo(() => ({ status, q, filters }), [status, q, filters])
  const requestId = useRef(0)

  const loadAgents = useCallback(() => {
    getLibraryAgents(token)
      .then(data => {
        setAgents(data.agents)
        setSync(data.sync)
        setAgentId(current => current || data.agents[0]?.id || "")
      })
      .catch(e => setError(e.message))
  }, [token])

  const load = useCallback(() => {
    if (!agentId) return
    const id = ++requestId.current
    searchLibrary(agentId, selection, page, token)
      .then(data => { if (id === requestId.current) setResult(data) })
      .catch(e => setError(e.message))
  }, [agentId, selection, page, token])

  useEffect(() => { loadAgents() }, [loadAgents])
  useEffect(() => { load() }, [load])
  useEffect(() => { setPage(1); setSelected(new Set()) }, [agentId, selection])

  // While something is moving through the pipeline, keep the view fresh.
  const moving = result
    ? result.counts.queued + result.counts.processing + result.counts.indexing > 0
    : false
  useEffect(() => {
    if (!moving && !sync?.running) return
    const timer = setInterval(() => { load(); if (sync?.running) loadAgents() }, 5000)
    return () => clearInterval(timer)
  }, [moving, sync?.running, load, loadAgents])

  const setFilter = (name: string, value: string | { from?: string; to?: string }) => {
    setFilters(prev => {
      const next = { ...prev }
      const empty = typeof value === "string" ? !value : !value.from && !value.to
      if (empty) delete next[name]
      else next[name] = value
      return next
    })
  }

  const run = async (action: LibraryActionName, target: { keys: string[] } | { selection: LibrarySelection }) => {
    setBusy(true)
    setError("")
    try {
      const res = await runLibraryAction(agentId, action, target, token)
      setNotice(`${num(res.moved)} מסמכים עודכנו`)
      setSelected(new Set())
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : "הפעולה נכשלה")
    } finally {
      setBusy(false)
    }
  }

  const runBulk = (action: (typeof ACTIONS)[number]) => {
    if (selected.size > 0) {
      if (action.confirm && !window.confirm(action.confirm)) return
      run(action.name, { keys: Array.from(selected) })
      return
    }
    const count = Math.min(bulkLimit, result?.total ?? 0)
    if (!window.confirm(`${action.label}: עד ${num(count)} מסמכים מתוך תוצאות החיפוש. להמשיך?`)) return
    run(action.name, { selection: { ...selection, limit: bulkLimit } })
  }

  const startSync = async () => {
    setError("")
    try {
      await syncCatalog(agentId, token)
      loadAgents()
    } catch (e) {
      setError(e instanceof Error ? e.message : "העדכון נכשל")
    }
  }

  const docs = result?.documents ?? []
  const allOnPageSelected = docs.length > 0 && docs.every(d => selected.has(d.record_key))
  const togglePage = () => {
    setSelected(prev => {
      const next = new Set(prev)
      docs.forEach(d => (allOnPageSelected ? next.delete(d.record_key) : next.add(d.record_key)))
      return next
    })
  }
  const pageCount = result ? Math.max(1, Math.ceil(result.total / result.page_size)) : 1
  const filterFields = (agent?.filters ?? []).filter(f => !f.name.includes("Header"))

  if (!agents.length) {
    return error
      ? <ErrorNote message={error} />
      : <p className="text-sm text-muted-foreground">טוען…</p>
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {agents.length > 1 && (
          <select className={selectClass} value={agentId} onChange={e => setAgentId(e.target.value)}>
            {agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        )}
        <h2 className="text-xl font-semibold tracking-tight">{agent?.name}</h2>
        <span className="flex-1" />
        {result && (
          <Pill className={result.worker.online ? TONE.emerald : TONE.neutral}>
            סוכן האינדוקס {result.worker.online ? "מחובר" : `לא מחובר · נראה ${relativeTime(result.worker.last_seen)}`}
          </Pill>
        )}
        <Button size="sm" variant="outline" disabled={sync?.running} onClick={startSync}>
          <RefreshCw className={cn("ms-1 h-3.5 w-3.5", sync?.running && "animate-spin")} />
          {sync?.running ? `מעדכן קטלוג… (${num(sync.added)} חדשים)` : "בדוק פרסומים חדשים"}
        </Button>
      </div>

      {result && !result.worker.online && result.counts.queued > 0 && (
        <p className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-800 dark:border-amber-400/20 dark:bg-amber-400/10 dark:text-amber-300">
          {num(result.counts.queued)} מסמכים ממתינים בתור. כדי שיורדו ויפורסרו, הפעל במחשב שלך את
          <span dir="ltr" className="mx-1 font-mono text-xs">scripts\start-indexer.bat</span>
        </p>
      )}
      {sync?.error && <ErrorNote message={`עדכון הקטלוג נכשל: ${sync.error}`} />}
      <ErrorNote message={error} />

      {result && result.index_cost.documents > 0 && (
        <IndexCostSummary cost={result.index_cost} catalogTotal={result.catalog_total} indexed={result.counts.indexed} />
      )}

      {/* status chips */}
      <div className="flex flex-wrap gap-2">
        <button onClick={() => setStatus(null)} className="chip" data-active={status === null}>
          הכל: {num(result?.catalog_total)}
        </button>
        {DOC_STATUS_ORDER.map(s => (
          <button
            key={s}
            onClick={() => setStatus(status === s ? null : s)}
            className={cn("chip", !result?.counts[s] && status !== s && "opacity-50")}
            data-active={status === s}
          >
            {DOC_STATUS[s].label}: {num(result?.counts[s])}
          </button>
        ))}
      </div>

      {/* filters */}
      <div className={cn(CARD, "flex flex-wrap items-end gap-3 p-4")}>
        <label className="grow text-xs text-muted-foreground">
          חיפוש בכותרת
          <Input className="mt-1 h-9" value={q} onChange={e => setQ(e.target.value)} placeholder="שם צד, רחוב, גוש…" />
        </label>
        {filterFields.map(f => {
          if (f.type === "choice") {
            return (
              <label key={f.name} className="text-xs text-muted-foreground">
                {f.label}
                <select
                  className={cn(selectClass, "mt-1 block max-w-[11rem]")}
                  value={(filters[f.name] as string) ?? ""}
                  onChange={e => setFilter(f.name, e.target.value)}
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
                    onChange={e => setFilter(f.name, { ...value, from: e.target.value })} />
                  –
                  <Input type="date" className="h-9 w-[8.5rem]" value={value.to ?? ""}
                    onChange={e => setFilter(f.name, { ...value, to: e.target.value })} />
                </span>
              </label>
            )
          }
          return (
            <label key={f.name} className="text-xs text-muted-foreground">
              {f.label}
              <Input className="mt-1 h-9 w-24" value={(filters[f.name] as string) ?? ""}
                onChange={e => setFilter(f.name, e.target.value)} />
            </label>
          )
        })}
        {(q || Object.keys(filters).length > 0) && (
          <Button size="sm" variant="ghost" onClick={() => { setQ(""); setFilters({}) }}>נקה</Button>
        )}
      </div>

      {/* actions */}
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground">
          {selected.size > 0
            ? `${num(selected.size)} מסמכים נבחרו:`
            : <>פעולה על תוצאות החיפוש ({num(result?.total)}), עד</>}
        </span>
        {selected.size === 0 && (
          <Input type="number" min={1} max={5000} className="h-8 w-20" value={bulkLimit}
            onChange={e => setBulkLimit(Math.max(1, Math.min(5000, Number(e.target.value) || 1)))} />
        )}
        {ACTIONS.map(a => (
          <Button key={a.name} size="sm" variant={a.name === "queue" ? "default" : "outline"}
            disabled={busy || !result?.total} onClick={() => runBulk(a)}>
            {a.label}
          </Button>
        ))}
        {selected.size > 0 && (
          <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>בטל בחירה</Button>
        )}
        {notice && <span className="text-xs text-emerald-600 dark:text-emerald-400">{notice}</span>}
      </div>

      {/* table */}
      <div className={cn(CARD, "overflow-x-auto")}>
        <table className="data-table">
          <thead>
            <tr>
              <th className="w-10">
                <input type="checkbox" checked={allOnPageSelected} onChange={togglePage} aria-label="בחר את כל העמוד" />
              </th>
              <th>מסמך</th>
              <th>פרסום</th>
              <th>מצב</th>
              <th>עמודים</th>
              <th>טבלאות</th>
              <th>קטעים</th>
              <th>עלות אינדוקס</th>
            </tr>
          </thead>
          <tbody>
            {docs.map(d => (
              <tr key={d.record_key}>
                <td className="text-center">
                  <input
                    type="checkbox"
                    checked={selected.has(d.record_key)}
                    onChange={() => setSelected(prev => {
                      const next = new Set(prev)
                      if (!next.delete(d.record_key)) next.add(d.record_key)
                      return next
                    })}
                    aria-label="בחר מסמך"
                  />
                </td>
                <td className="max-w-[34rem]">
                  <button className="block w-full truncate text-right underline-offset-4 hover:underline"
                    title={d.title} onClick={() => setOpenKey(d.record_key)}>
                    {d.title}
                  </button>
                  {d.error && <p className="mt-0.5 truncate text-xs text-red-600 dark:text-red-400" title={d.error}>{d.error}</p>}
                </td>
                <td className="whitespace-nowrap text-xs text-muted-foreground">{shortDate(d.sort_date)}</td>
                <td><Pill className={DOC_STATUS[d.status].className}>{DOC_STATUS[d.status].label}</Pill></td>
                <td className="tabular-nums">{d.pages ?? "—"}</td>
                <td className="tabular-nums">
                  {d.tables ?? "—"}
                  {!!d.flagged_tables && (
                    <span className="ms-1 text-xs text-amber-600 dark:text-amber-400" title="טבלאות שסומנו לבדיקה">
                      ({d.flagged_tables} לבדיקה)
                    </span>
                  )}
                </td>
                <td className="tabular-nums">{d.chunks ?? "—"}</td>
                <td className="tabular-nums" title={d.embed_tokens ? `${num(d.embed_tokens)} טוקנים` : undefined}>
                  {usdPrecise(d.index_cost_usd)}
                </td>
              </tr>
            ))}
            {result && docs.length === 0 && (
              <tr>
                <td colSpan={8} className="py-12 text-center text-sm text-muted-foreground">
                  {result.catalog_total === 0
                    ? "הקטלוג ריק. טען אותו פעם אחת מהמחשב: python scripts/index_worker.py push-catalog"
                    : "אין מסמכים שמתאימים לסינון."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {result && result.total > result.page_size && (
        <div className="flex items-center justify-center gap-3 text-sm">
          <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>הקודם</Button>
          <span className="text-muted-foreground">עמוד {num(page)} מתוך {num(pageCount)}</span>
          <Button size="sm" variant="outline" disabled={page >= pageCount} onClick={() => setPage(page + 1)}>הבא</Button>
        </div>
      )}

      <DocumentViewer
        agentId={agentId}
        recordKey={openKey}
        token={token}
        onClose={() => setOpenKey(null)}
        onAction={async (action, key) => {
          await runLibraryAction(agentId, action, { keys: [key] }, token)
          load()
        }}
      />
    </div>
  )
}
