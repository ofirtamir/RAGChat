"use client"

import { Fragment, useCallback, useEffect, useRef, useState } from "react"
import { ChevronDown, ChevronLeft, Pencil, Plus, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  deleteEvalQuestion, EvalQuestion, EvalRun, EvalSet, getEvalQuestions, QuestionPage, QuestionSource,
} from "@/lib/admin-eval-api"
import { cn } from "@/lib/utils"
import { formatMetric, ModePill, runName, SOURCE_LABEL, typeLabel } from "./benchmark-labels"
import { selectClass } from "./filter-fields"
import { CARD, ErrorNote, num, Pill, TONE } from "./shared"

/** The set's questions, with the result of the selected run per question. */
export function QuestionsTable({ token, set, runs, runId, onRunChange, reloadKey, onLabel, onAdd, onOpenDoc }: {
  token: string | undefined
  set: EvalSet
  runs: EvalRun[]
  runId: string | null
  onRunChange: (id: string | null) => void
  reloadKey: number
  onLabel: (q: EvalQuestion) => void
  onAdd: () => void
  onOpenDoc: (recordKey: string) => void
}) {
  const [type, setType] = useState<string | null>(null)
  const [source, setSource] = useState<QuestionSource | null>(null)
  const [missed, setMissed] = useState(false)
  const [unlabelled, setUnlabelled] = useState(false)
  const [q, setQ] = useState("")
  const [page, setPage] = useState(1)
  const [data, setData] = useState<QuestionPage | null>(null)
  const [error, setError] = useState("")
  const [open, setOpen] = useState<string | null>(null)
  const requestId = useRef(0)
  const setRuns = runs.filter(r => r.set_id === set.id && r.status !== "running")

  const load = useCallback(() => {
    const id = ++requestId.current
    getEvalQuestions(set.id, { type, source, q, run_id: runId, missed: missed && !!runId, unlabelled, page }, token)
      .then(d => { if (id === requestId.current) { setData(d); setError("") } })
      .catch(e => setError(e.message))
  }, [set.id, type, source, q, runId, missed, unlabelled, page, token])

  useEffect(() => { load() }, [load, reloadKey])
  useEffect(() => { setPage(1) }, [set.id, type, source, q, runId, missed, unlabelled])
  useEffect(() => { setType(null); setSource(null) }, [set.id])

  const remove = async (question: EvalQuestion) => {
    if (!confirm(`למחוק את השאלה ״${question.question.slice(0, 60)}״ מהסט?`)) return
    try {
      await deleteEvalQuestion(question.id, token)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : "המחיקה נכשלה")
    }
  }

  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1
  const facets = data?.facets

  return (
    <div className={cn(CARD, "overflow-hidden")}>
      <div className="space-y-2 border-b px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <p className="me-2 text-sm font-medium">שאלות בסט {data && <span className="text-muted-foreground">({num(data.total)})</span>}</p>
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            תוצאות של
            <select className={cn(selectClass, "h-8 text-xs")} value={runId ?? ""} onChange={e => onRunChange(e.target.value || null)}>
              <option value="">ללא ריצה</option>
              {setRuns.map(r => (
                <option key={r.id} value={r.id}>{runName(r)} · {new Date(r.started_at).toLocaleDateString("he-IL")}</option>
              ))}
            </select>
          </label>
          <span className="flex-1" />
          <Input className="h-8 w-44 text-xs" placeholder="חיפוש בשאלות" value={q} onChange={e => setQ(e.target.value)} />
          <Button size="sm" variant="outline" className="h-8 gap-1" onClick={onAdd}><Plus className="h-3.5 w-3.5" /> שאלה ידנית</Button>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <button className="chip" data-active={type === null} onClick={() => setType(null)}>כל הסוגים</button>
          {Object.entries(facets?.types ?? {}).map(([t, n]) => (
            <button key={t} className="chip" data-active={type === t} onClick={() => setType(t)}>{typeLabel(t)} ({num(n)})</button>
          ))}
          <span className="mx-1 h-4 w-px bg-border" />
          <button className="chip" data-active={source === null} onClick={() => setSource(null)}>כל המקורות</button>
          {(Object.keys(facets?.sources ?? {}) as QuestionSource[]).map(s => (
            <button key={s} className="chip" data-active={source === s} onClick={() => setSource(s)}>{SOURCE_LABEL[s] ?? s}</button>
          ))}
          <span className="mx-1 h-4 w-px bg-border" />
          <label className={cn("flex items-center gap-1.5 text-xs", !runId && "opacity-50")}>
            <input type="checkbox" checked={missed} disabled={!runId} onChange={e => setMissed(e.target.checked)} />
            רק שאלות שהחיפוש פספס
          </label>
          <label className="flex items-center gap-1.5 text-xs">
            <input type="checkbox" checked={unlabelled} onChange={e => setUnlabelled(e.target.checked)} />
            רק בלי החלטה נכונה
          </label>
        </div>
      </div>
      <ErrorNote message={error} />
      <div className="overflow-x-auto">
        <table className="data-table">
          <thead>
            <tr>
              <th className="w-6" />
              <th>שאלה</th>
              <th>סוג</th>
              <th>ההחלטה הנכונה</th>
              <th title="המקום של התוצאה הראשונה מההחלטה הנכונה ברשימה שהחיפוש החזיר">תוצאה</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(data?.questions ?? []).map(row => (
              <Fragment key={row.id}>
                <tr className="cursor-pointer" onClick={() => setOpen(open === row.id ? null : row.id)}>
                  <td className="text-muted-foreground">
                    {open === row.id ? <ChevronDown className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
                  </td>
                  <td className="max-w-[26rem]">
                    <p className="line-clamp-2">{row.question}</p>
                    <p className="text-xs text-muted-foreground">{SOURCE_LABEL[row.source]}</p>
                  </td>
                  <td className="whitespace-nowrap text-xs">{typeLabel(row.type)}</td>
                  <td className="max-w-[18rem]" onClick={e => e.stopPropagation()}>
                    <GoldCell row={row} onOpenDoc={onOpenDoc} />
                  </td>
                  <td className="whitespace-nowrap"><ResultCell row={row} hasRun={!!runId} /></td>
                  <td onClick={e => e.stopPropagation()}>
                    <div className="flex gap-0.5">
                      <Button size="icon" variant="ghost" className="h-7 w-7" title="סמן החלטה נכונה" onClick={() => onLabel(row)}>
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                      <Button size="icon" variant="ghost" className="h-7 w-7" title="מחק שאלה" onClick={() => remove(row)}>
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </td>
                </tr>
                {open === row.id && (
                  <tr>
                    <td colSpan={6} className="bg-muted/40">
                      <Retrieved row={row} run={runs.find(r => r.id === runId)} onOpenDoc={onOpenDoc} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
            {data && data.questions.length === 0 && (
              <tr><td colSpan={6} className="py-10 text-center text-sm text-muted-foreground">אין שאלות להצגה.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="flex items-center justify-center gap-2 border-t py-2 text-xs">
          <Button size="sm" variant="ghost" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>הקודם</Button>
          <span className="tabular-nums">{page} / {pages}</span>
          <Button size="sm" variant="ghost" disabled={page >= pages} onClick={() => setPage(p => p + 1)}>הבא</Button>
        </div>
      )}
    </div>
  )
}

function GoldCell({ row, onOpenDoc }: { row: EvalQuestion; onOpenDoc: (key: string) => void }) {
  const meta = Object.entries(row.expected_meta)
  if (!row.gold.length && !meta.length) return <Pill className={TONE.amber}>לא סומנה</Pill>
  return (
    <div className="space-y-0.5 text-xs">
      {row.gold.map(g => (
        <button key={g.record_key} onClick={() => onOpenDoc(g.record_key)}
          className="block max-w-full truncate text-start underline-offset-2 hover:underline">
          {g.title ?? g.record_key.slice(0, 10)}
        </button>
      ))}
      {!row.gold.length && meta.length > 0 && (
        <p className="truncate text-muted-foreground">תוצאות עם: {meta.map(([k, v]) => `${k}=${v}`).join(", ")}</p>
      )}
    </div>
  )
}

function ResultCell({ row, hasRun }: { row: EvalQuestion; hasRun: boolean }) {
  const r = row.result
  if (!hasRun) return <span className="text-xs text-muted-foreground">—</span>
  if (!r) return <span className="text-xs text-muted-foreground">לא נמדדה</span>
  if (!row.gold.length) {
    const acc = r.metrics["filter_acc@10"]
    return <span className="text-xs">דיוק סינון {formatMetric("filter_acc@10", acc)}</span>
  }
  if (r.found_rank == null) return <Pill className={TONE.red}>פוספסה</Pill>
  return (
    <Pill className={r.found_rank === 1 ? TONE.emerald : r.found_rank <= 10 ? TONE.sky : TONE.amber}>
      נמצאה במקום {r.found_rank}
    </Pill>
  )
}

function Retrieved({ row, run, onOpenDoc }: {
  row: EvalQuestion
  run: EvalRun | undefined
  onOpenDoc: (key: string) => void
}) {
  const r = row.result
  if (!r) {
    return <p className="px-2 py-3 text-xs text-muted-foreground">בחר ריצה כדי לראות מה החיפוש החזיר לשאלה הזו.</p>
  }
  const gold = new Set(row.gold.map(g => g.record_key))
  return (
    <div className="space-y-2 px-2 py-3 text-xs">
      <div className="flex flex-wrap items-center gap-2 text-muted-foreground">
        {run && <ModePill mode={run.mode} />}
        <span>10 התוצאות הראשונות (כל שורה היא קטע; ירוק = מההחלטה הנכונה)</span>
        {r.search_queries && r.search_queries.length > 0 && (
          <span>· שאילתות החיפוש: {r.search_queries.map(s => `״${s}״`).join(" · ")}</span>
        )}
      </div>
      {r.error && <p className="break-words text-red-600 dark:text-red-400">{r.error}</p>}
      <ol className="space-y-1">
        {r.top.map(t => (
          <li key={`${t.rank}-${t.record_key}`}
            className={cn("flex items-baseline gap-2 rounded-lg px-2.5 py-1.5",
              gold.has(t.record_key) ? "bg-emerald-50 dark:bg-emerald-400/10" : "bg-background")}>
            <span className="w-6 shrink-0 tabular-nums text-muted-foreground">{t.rank}</span>
            <button onClick={() => onOpenDoc(t.record_key)} className="min-w-0 truncate text-start underline-offset-2 hover:underline">
              {t.title ?? t.record_key.slice(0, 10)}
            </button>
            <span className="min-w-0 shrink truncate text-muted-foreground">{t.section || ""}</span>
          </li>
        ))}
        {r.top.length === 0 && <li className="text-muted-foreground">החיפוש לא החזיר תוצאות.</li>}
      </ol>
      {row.gold_section && <p className="text-muted-foreground">הסעיף שממנו נכתבה השאלה: {row.gold_section}</p>}
    </div>
  )
}
