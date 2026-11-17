"use client"

import { useEffect, useState } from "react"
import { Search } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import type { AgentFilters } from "@/lib/api"
import { getLibraryAgents, LibraryAgent } from "@/lib/admin-api"
import { runSearchLab, SearchLabResult, SearchLabRow } from "@/lib/admin-insights-api"
import { cn } from "@/lib/utils"
import { ChunkCard } from "./chunk-viewer"
import { DocumentViewer } from "./document-viewer"
import { FilterFields, selectClass, withFilter } from "./filter-fields"
import { CARD, ErrorNote, num, Pill, TONE } from "./shared"

/** Ask a question the way the chat would and see what the search finds, stage by stage — without answering it. */
export function SearchLabTab({ token }: { token: string | undefined }) {
  const [agents, setAgents] = useState<LibraryAgent[]>([])
  const [agentId, setAgentId] = useState("")
  const [query, setQuery] = useState("")
  const [filters, setFilters] = useState<AgentFilters>({})
  const [rerank, setRerank] = useState(false)
  const [result, setResult] = useState<SearchLabResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [expanded, setExpanded] = useState<string | null>(null)
  const [onlyTop, setOnlyTop] = useState(true)
  const [openDoc, setOpenDoc] = useState<string | null>(null)

  useEffect(() => {
    getLibraryAgents(token)
      .then(d => { setAgents(d.agents); setAgentId(id => id || d.agents[0]?.id || "") })
      .catch(e => setError(e.message))
  }, [token])
  const agent = agents.find(a => a.id === agentId)

  const run = async () => {
    if (!query.trim() || !agentId) return
    setBusy(true)
    setError("")
    try {
      setResult(await runSearchLab(agentId, query.trim(), filters, rerank, token))
      setExpanded(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : "החיפוש נכשל")
    } finally {
      setBusy(false)
    }
  }

  const rows = (result?.rows ?? []).filter(r => !onlyTop || r.in_top_k)
  const sorted = result?.reranked
    ? [...rows].sort((a, b) => (a.final_rank ?? 999) - (b.final_rank ?? 999) || a.fused_rank - b.fused_rank)
    : rows

  return (
    <div className="space-y-4">
      <div className={cn(CARD, "space-y-3 p-4")}>
        <p className="text-sm text-muted-foreground">
          כתוב שאלה כמו שמשתמש היה כותב ובדוק מה החיפוש מוצא: איזה קטעים, באיזה דירוג, ולמה (משמעות או מילים מדויקות).
          החיפוש עצמו כמעט חינמי; ״דירוג מחדש״ מפעיל את מודל הדירוג כמו בצ׳אט ועולה כמו חלק קטן משאלה.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          {agents.length > 1 && (
            <select className={selectClass} value={agentId} onChange={e => setAgentId(e.target.value)}>
              {agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          )}
          <label className="min-w-[16rem] grow text-xs text-muted-foreground">
            שאלה
            <Input className="mt-1 h-9" value={query} onChange={e => setQuery(e.target.value)}
              onKeyDown={e => e.key === "Enter" && run()} placeholder="לדוגמה: איך חושב היטל השבחה בגין תוספת קומה?" />
          </label>
          <FilterFields fields={agent?.filters ?? []} filters={filters}
            onChange={(name, value) => setFilters(prev => withFilter(prev, name, value))} />
          <label className="flex h-9 items-center gap-1.5 text-xs">
            <input type="checkbox" checked={rerank} onChange={e => setRerank(e.target.checked)} />
            דירוג מחדש (כמו בצ׳אט)
          </label>
          <Button onClick={run} disabled={busy || !query.trim()} className="gap-1.5">
            <Search className="h-4 w-4" /> {busy ? "מחפש…" : "חפש"}
          </Button>
        </div>
      </div>

      <ErrorNote message={error} />

      {result && (
        <>
          {Object.entries(result.errors).map(([k, v]) => (
            <ErrorNote key={k} message={`${k === "semantic" ? "החיפוש הסמנטי נכשל — מוצגות רק התאמות מילים" : "חיפוש המילים נכשל"}: ${v}`} />
          ))}
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>{num(result.semantic_count)} התאמות משמעות · {num(result.keyword_count)} התאמות מילים</span>
            {result.keyword_terms && <span>· מילות חיפוש: <code className="rounded bg-muted px-1.5">{result.keyword_terms}</code></span>}
            {result.where && <span>· מסנן: <code dir="ltr" className="rounded bg-muted px-1.5">{result.where}</code></span>}
            <span className="flex-1" />
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={onlyTop} onChange={e => setOnlyTop(e.target.checked)} />
              רק {result.candidates} המועמדים שעוברים לדירוג
            </label>
          </div>
          <p className="text-xs text-muted-foreground">
            {result.reranked
              ? `ירוק = ${result.final_k} הקטעים שהיו נשלחים למודל התשובה (אחרי דירוג מחדש).`
              : `בצ׳אט, ${result.candidates} המועמדים הראשונים עוברים דירוג מחדש ו-${result.final_k} הטובים נשלחים למודל.`}
          </p>

          <div className={cn(CARD, "overflow-x-auto")}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>{result.reranked ? "סופי" : "דירוג"}</th>
                  <th>מסמך וסעיף</th>
                  <th title="מקום ברשימת החיפוש לפי משמעות (וקטורים)">משמעות</th>
                  <th title="מקום ברשימת החיפוש לפי מילים מדויקות (BM25)">מילים</th>
                  <th title="ציון המיזוג של שתי הרשימות (RRF)">מיזוג</th>
                  {result.reranked && <th title="ציון הרלוונטיות שנתן מודל הדירוג (0–10)">רלוונטיות</th>}
                </tr>
              </thead>
              <tbody>
                {sorted.map(r => (
                  <LabRow key={r.id} row={r} reranked={result.reranked} open={expanded === r.id}
                    onToggle={() => setExpanded(expanded === r.id ? null : r.id)} onOpenDoc={() => setOpenDoc(r.record_key)} />
                ))}
                {sorted.length === 0 && (
                  <tr><td colSpan={6} className="py-10 text-center text-sm text-muted-foreground">לא נמצאו קטעים. האם יש מסמכים באינדקס?</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}

      {openDoc && agentId && (
        <DocumentViewer agentId={agentId} recordKey={openDoc} token={token} initialView="chunks" onClose={() => setOpenDoc(null)} />
      )}
    </div>
  )
}

function LabRow({ row, reranked, open, onToggle, onOpenDoc }: {
  row: SearchLabRow
  reranked: boolean
  open: boolean
  onToggle: () => void
  onOpenDoc: () => void
}) {
  const chosen = reranked && row.final_rank != null
  return (
    <>
      <tr className={cn("cursor-pointer", chosen && "bg-emerald-50/60 dark:bg-emerald-400/5")} onClick={onToggle}>
        <td className="tabular-nums font-medium">{reranked ? (row.final_rank ?? "—") : row.fused_rank}</td>
        <td className="max-w-[28rem]">
          <p className="truncate">{row.source}</p>
          <p className="truncate text-xs text-muted-foreground">{row.section || "ללא סעיף"} · עמוד {row.page + 1} · קטע #{row.chunk_index}</p>
        </td>
        <td className="tabular-nums text-xs">
          {row.semantic_rank ?? "—"}
          {row.semantic_similarity != null && <span className="text-muted-foreground"> ({row.semantic_similarity.toFixed(2)})</span>}
        </td>
        <td className="tabular-nums text-xs">
          {row.keyword_rank ?? "—"}
          {row.keyword_score != null && <span className="text-muted-foreground"> ({row.keyword_score.toFixed(1)})</span>}
        </td>
        <td className="tabular-nums text-xs">{row.rrf.toFixed(4)}</td>
        {reranked && (
          <td>
            {row.rerank_score != null && (
              <Pill className={row.rerank_score >= 7 ? TONE.emerald : row.rerank_score >= 4 ? TONE.amber : TONE.neutral}>
                {row.rerank_score}/10
              </Pill>
            )}
          </td>
        )}
      </tr>
      {open && (
        <tr>
          <td colSpan={6} className="bg-muted/40">
            <ChunkCard chunk={row} raw={false}
              extra={<button onClick={onOpenDoc} className="ms-auto text-xs underline underline-offset-2">פתח את כל קטעי המסמך</button>} />
          </td>
        </tr>
      )}
    </>
  )
}
