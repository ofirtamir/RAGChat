"use client"

import { useEffect, useState } from "react"
import { GitCompareArrows, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { compareRuns, Comparison, EvalRun, MetricName } from "@/lib/admin-eval-api"
import { cn } from "@/lib/utils"
import {
  allRow, Delta, formatMetric, HEADLINE, METRIC_LABEL, ModePill, RUN_STATUS, runName, typeLabel,
} from "./benchmark-labels"
import { CARD, dateTime, ErrorNote, num, Panel, Pill, StatCard, TONE, usdPrecise } from "./shared"

/** Headline numbers of the latest finished run, with the change since the previous run of the same mode and set. */
export function MetricCards({ runs }: { runs: EvalRun[] }) {
  const finished = runs.filter(r => r.status === "done" && r.summary)
  const latest = finished[0]
  if (!latest) {
    return (
      <div className={cn(CARD, "p-5 text-sm text-muted-foreground")}>
        עדיין אין מדידה לסט הזה. בחר ״הרץ מדידה״ — ריצת ״חיפוש בלבד״ כמעט חינמית ולוקחת דקה-שתיים.
      </div>
    )
  }
  const previous = finished.find(r => r.id !== latest.id && r.mode === latest.mode && r.set_id === latest.set_id)
  const now = allRow(latest)!, before = allRow(previous)
  return (
    <div className="space-y-2">
      <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        המדידה האחרונה: <span className="font-medium text-foreground">{runName(latest)}</span>
        <ModePill mode={latest.mode} /> · {dateTime(latest.finished_at ?? latest.started_at)} · {num(now.n)} שאלות
        {previous ? <> · השינוי לעומת ״{runName(previous)}״</> : <> · אין ריצה קודמת מאותו סוג להשוואה</>}
      </p>
      {latest.degraded > 0 && (
        <ErrorNote message={`${num(latest.degraded)} שאלות רצו בלי החיפוש הסמנטי (יצירת הווקטור נכשלה) — אי אפשר להשוות את הריצה הזו לריצה רגילה.`} />
      )}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {HEADLINE.map(m => {
          const value = now[m.id]
          const prev = before?.[m.id]
          return (
            <StatCard key={m.id} label={m.label} value={formatMetric(m.id, value)}
              hint={<>
                {value != null && prev != null && (
                  <span className="me-1.5 font-medium"><Delta metric={m.id} value={value - prev} /></span>
                )}
                {m.hint}
              </>} />
          )
        })}
      </div>
    </div>
  )
}

/** Every run, newest first. A row selects the run whose results the questions table shows; two ticks compare. */
export function RunsTable({ runs, selected, onSelect, token }: {
  runs: EvalRun[]
  selected: string | null
  onSelect: (id: string) => void
  token: string | undefined
}) {
  const [picked, setPicked] = useState<string[]>([])
  const [comparison, setComparison] = useState<Comparison | null>(null)
  const [error, setError] = useState("")

  useEffect(() => { setPicked(p => p.filter(id => runs.some(r => r.id === id))) }, [runs])

  const toggle = (id: string) =>
    setPicked(p => (p.includes(id) ? p.filter(x => x !== id) : [...p, id].slice(-2)))

  const compare = async () => {
    // A = the older run, B = the newer one
    const [b, a] = runs.filter(r => picked.includes(r.id))
    setError("")
    try {
      setComparison(await compareRuns(a.id, b.id, token))
    } catch (e) {
      setError(e instanceof Error ? e.message : "ההשוואה נכשלה")
    }
  }

  return (
    <div className="space-y-3">
      <Panel title={`ריצות (${num(runs.length)})`} className="overflow-hidden px-0 pb-0 [&>div:first-child]:px-5"
        action={
          <Button size="sm" variant="outline" className="h-8 gap-1.5" disabled={picked.length !== 2} onClick={compare}>
            <GitCompareArrows className="h-3.5 w-3.5" /> השווה בין שתי ריצות מסומנות
          </Button>
        }>
        <div className="overflow-x-auto">
          <table className="data-table text-xs">
            <thead>
              <tr>
                <th />
                <th>מתי</th>
                <th>תווית</th>
                <th>סוג</th>
                <th>סט</th>
                <th>שאלות</th>
                {HEADLINE.map(m => <th key={m.id} title={m.hint}>{m.label}</th>)}
                <th title="לפי ספירת הטוקנים של קריאות המודל; הווקטורים לפי אורך השאלות">עלות</th>
                <th>מצב</th>
              </tr>
            </thead>
            <tbody>
              {runs.map(r => {
                const row = allRow(r)
                return (
                  <tr key={r.id} onClick={() => r.status !== "running" && onSelect(r.id)}
                    className={cn("cursor-pointer", selected === r.id && "bg-sky-50/70 dark:bg-sky-400/5")}>
                    <td onClick={e => e.stopPropagation()}>
                      <input type="checkbox" aria-label="סמן להשוואה" checked={picked.includes(r.id)}
                        disabled={r.status !== "done"} onChange={() => toggle(r.id)} />
                    </td>
                    <td className="whitespace-nowrap text-muted-foreground">{dateTime(r.started_at)}</td>
                    <td className="max-w-[12rem] truncate font-medium">{runName(r)}</td>
                    <td><ModePill mode={r.mode} /></td>
                    <td className="max-w-[10rem] truncate">{r.set_name ?? "—"}</td>
                    <td className="tabular-nums">{r.status === "running" ? `${num(r.done)}/${num(r.n_questions)}` : num(r.n_questions)}</td>
                    {HEADLINE.map(m => <td key={m.id} className="tabular-nums">{formatMetric(m.id, row?.[m.id])}</td>)}
                    <td className="tabular-nums" title={`הערכה מראש: ${usdPrecise(r.cost_estimate)}`}>{usdPrecise(r.cost_usd)}</td>
                    <td>
                      <Pill className={RUN_STATUS[r.status].className}>{RUN_STATUS[r.status].label}</Pill>
                      {r.error && <p className="mt-0.5 max-w-[14rem] truncate text-red-600 dark:text-red-400" title={r.error}>{r.error}</p>}
                      {r.degraded > 0 && <p className="mt-0.5 text-amber-600 dark:text-amber-400">{num(r.degraded)} בלי חיפוש סמנטי</p>}
                    </td>
                  </tr>
                )
              })}
              {runs.length === 0 && (
                <tr><td colSpan={11} className="py-8 text-center text-muted-foreground">עדיין אין ריצות.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>
      <ErrorNote message={error} />
      {comparison && <ComparePanel data={comparison} onClose={() => setComparison(null)} />}
    </div>
  )
}

const COMPARE_METRICS: MetricName[] = ["recall@5", "recall@10", "recall@20", "mrr@20", "hit@1", "distinct@10",
  "section_hit@10", "filter_acc@10", "vector_recall@20", "keyword_recall@20", "candidate_recall@20"]

function ComparePanel({ data, onClose }: { data: Comparison; onClose: () => void }) {
  const types = Object.keys(data.deltas).filter(t => t !== "all")
  const all = data.deltas.all ?? {}
  return (
    <div className={cn(CARD, "space-y-4 p-5")}>
      <div className="flex flex-wrap items-start gap-2">
        <div className="space-y-1 text-sm">
          <p><span className="text-muted-foreground">לפני (A):</span> <b>{runName(data.a)}</b> · {dateTime(data.a.started_at)} <ModePill mode={data.a.mode} /></p>
          <p><span className="text-muted-foreground">אחרי (B):</span> <b>{runName(data.b)}</b> · {dateTime(data.b.started_at)} <ModePill mode={data.b.mode} /></p>
          <p className="text-xs text-muted-foreground">{num(data.shared)} שאלות משותפות לשתי הריצות</p>
        </div>
        <span className="flex-1" />
        <Button size="icon" variant="ghost" className="h-8 w-8" onClick={onClose} aria-label="סגור"><X className="h-4 w-4" /></Button>
      </div>
      {!data.same_set && <ErrorNote message="הריצות נעשו על סטים שונים של שאלות — ההשוואה לא אמינה." />}

      <div className="grid gap-4 lg:grid-cols-2">
        <table className="data-table text-xs">
          <thead><tr><th>מדד (כל השאלות)</th><th>A</th><th>B</th><th>שינוי</th></tr></thead>
          <tbody>
            {COMPARE_METRICS.filter(m => all[m]).map(m => (
              <tr key={m}>
                <td>{METRIC_LABEL[m]}</td>
                <td className="tabular-nums">{formatMetric(m, all[m]!.a)}</td>
                <td className="tabular-nums">{formatMetric(m, all[m]!.b)}</td>
                <td><Delta metric={m} value={all[m]!.delta} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        <table className="data-table text-xs">
          <thead><tr><th>סוג שאלה</th><th>שאלות</th><th>Recall@10</th><th>MRR</th><th>Hit@1</th></tr></thead>
          <tbody>
            {types.map(t => {
              const row = data.deltas[t]
              return (
                <tr key={t}>
                  <td>{typeLabel(t)}</td>
                  <td className="tabular-nums">{row.n?.b ?? row.n?.a ?? "—"}</td>
                  {(["recall@10", "mrr@20", "hit@1"] as MetricName[]).map(m => (
                    <td key={m}>
                      {row[m] ? <>{formatMetric(m, row[m]!.b)} <Delta metric={m} value={row[m]!.delta} /></> : "—"}
                    </td>
                  ))}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <FlipList title={`השתפרו (${num(data.gained_total)})`} tone={TONE.emerald} rows={data.gained} />
        <FlipList title={`הורעו (${num(data.lost_total)})`} tone={TONE.red} rows={data.lost} />
      </div>
      <p className="text-xs text-muted-foreground">
        ״השתפרו״ / ״הורעו״ — שאלות שההחלטה הנכונה נכנסה ל-10 התוצאות הראשונות או יצאה מהן. המספרים: המקום של ההחלטה הנכונה בכל ריצה.
      </p>
    </div>
  )
}

function FlipList({ title, tone, rows }: { title: string; tone: string; rows: Comparison["gained"] }) {
  const place = (rank: number | null) => (rank == null ? "לא נמצאה" : `#${rank}`)
  return (
    <div>
      <p className="mb-2 text-[13px] font-medium"><Pill className={tone}>{title}</Pill></p>
      <ul className="scrollbar-thin max-h-72 space-y-1 overflow-y-auto text-xs">
        {rows.map(r => (
          <li key={r.id} className="flex items-baseline gap-2 rounded-lg bg-muted/40 px-2.5 py-1.5">
            <span className="min-w-0 flex-1 truncate" title={r.question}>{r.question}</span>
            <span className="shrink-0 text-muted-foreground">{typeLabel(r.type)}</span>
            <span dir="ltr" className="shrink-0 tabular-nums">{place(r.rank_a)} → {place(r.rank_b)}</span>
          </li>
        ))}
        {rows.length === 0 && <li className="text-muted-foreground">אין.</li>}
      </ul>
    </div>
  )
}
