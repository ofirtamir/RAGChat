"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { Download } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { getLibraryAgents, LibraryAgent } from "@/lib/admin-api"
import {
  downloadExport, getChunkQuality, getQuality, getQuestions, LibraryChunkQuality, QualitySummary,
  QuestionFlag, QuestionPage, QuestionQuery,
} from "@/lib/admin-insights-api"
import { cn } from "@/lib/utils"
import { CHUNK_FLAG } from "./chunk-viewer"
import { DocumentViewer } from "./document-viewer"
import { QuestionDialog, RatingIcon, ROUTE_LABEL } from "./question-dialog"
import {
  CARD, DailyBars, dateTime, ErrorNote, num, Panel, pct, Pill, RangeChips, seconds, StatCard, TONE, usdPrecise,
} from "./shared"

const FLAGS: { id: QuestionFlag | null; label: string }[] = [
  { id: null, label: "הכל" },
  { id: "down", label: "👎 לא עזר" },
  { id: "up", label: "👍 עזר" },
  { id: "no_answer", label: "לא נמצאה תשובה" },
  { id: "no_sources", label: "בלי מקורות" },
  { id: "error", label: "שגיאות" },
  { id: "slow", label: "איטיות (30+ שנ׳)" },
]

const REFUSAL: Record<string, string> = { daily_limit: "מכסה יומית", monthly_budget: "תקרה חודשית" }

export function QualityTab({ token }: { token: string | undefined }) {
  const [days, setDays] = useState(30)
  const [summary, setSummary] = useState<QualitySummary | null>(null)
  const [error, setError] = useState("")
  const [openDoc, setOpenDoc] = useState<{ agentId: string; key: string } | null>(null)
  const [agents, setAgents] = useState<LibraryAgent[]>([])

  useEffect(() => {
    getQuality(days, token).then(setSummary).catch(e => setError(e.message))
  }, [days, token])
  useEffect(() => {
    getLibraryAgents(token).then(d => setAgents(d.agents)).catch(() => {})
  }, [token])

  if (error) return <ErrorNote message={error} />
  if (!summary) return <p className="text-sm text-muted-foreground">טוען…</p>
  const s = summary
  const answered = s.questions - s.errors
  const agentId = agents[0]?.id ?? ""

  return (
    <div className="space-y-4">
      <RangeChips value={days} onChange={setDays} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="שביעות רצון" value={pct(s.satisfaction)}
          tone={s.satisfaction != null && s.satisfaction < 0.7 ? "warn" : s.satisfaction != null ? "ok" : undefined}
          hint={`👍 ${num(s.up)} · 👎 ${num(s.down)} · ${pct(s.questions ? s.feedback / s.questions : null)} מהשאלות דורגו`} />
        <StatCard label="לא נמצאה תשובה במסמכים" value={pct(answered ? (s.no_answer + s.no_sources) / answered : null)}
          tone={answered && (s.no_answer + s.no_sources) / answered > 0.15 ? "warn" : undefined}
          hint={`${num(s.no_answer)} תשובות ״לא מצאתי״ · ${num(s.no_sources)} בלי מקורות — פערי תוכן`} />
        <StatCard label="זמן תגובה (חציון)" value={seconds(s.latency_ms.p50)}
          hint={`95% עד ${seconds(s.latency_ms.p95)} · מילה ראשונה ${seconds(s.first_token_ms.p50)}`}
          tone={s.latency_ms.p50 != null && s.latency_ms.p50 > 20000 ? "warn" : undefined} />
        <StatCard label="שגיאות" value={pct(s.questions ? s.errors / s.questions : null, 1)}
          tone={s.errors > 0 ? "warn" : undefined}
          hint={`${num(s.errors)} מתוך ${num(s.questions)} שאלות`} />
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="דירוגים ביום (ירוק 👍 · אדום 👎)">
          <DailyBars days={s.daily} pick={d => d.up} format={n => `${n} 👍`} height="h-20"
            barClassName="bg-emerald-500/70 hover:bg-emerald-500" />
          <div className="mt-1" />
          <DailyBars days={s.daily} pick={d => d.down} format={n => `${n} 👎`} height="h-14"
            barClassName="bg-red-500/70 hover:bg-red-500" />
        </Panel>
        <Panel title="איך נענו השאלות">
          <div className="space-y-2">
            {(Object.keys(ROUTE_LABEL) as (keyof typeof ROUTE_LABEL)[]).map(r => {
              const n = s.routes[r] ?? 0
              return (
                <div key={r} className="flex items-center gap-3 text-sm">
                  <span className="w-32 shrink-0 text-muted-foreground">{ROUTE_LABEL[r].label}</span>
                  <div className="h-2 flex-1 rounded-full bg-muted">
                    <div className="h-2 rounded-full bg-foreground/60" style={{ width: answered ? `${(n / answered) * 100}%` : 0 }} />
                  </div>
                  <span className="w-16 text-left tabular-nums">{num(n)}</span>
                </div>
              )
            })}
          </div>
          {s.refusals.length > 0 && (
            <p className="mt-4 text-xs text-amber-700 dark:text-amber-300">
              בקשות שנחסמו: {s.refusals.map(r => `${REFUSAL[r.reason] ?? r.reason} ${num(r.n)} (${num(r.users)} משתמשים)`).join(" · ")}
            </p>
          )}
        </Panel>
      </div>

      <QuestionLog token={token} days={days} />

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="המסמכים שהתשובות נשענות עליהם הכי הרבה">
          <table className="data-table text-xs">
            <thead><tr><th>מסמך</th><th>שאלות</th><th>👎</th></tr></thead>
            <tbody>
              {s.top_documents.map(d => (
                <tr key={d.record_key} className="cursor-pointer" onClick={() => agentId && setOpenDoc({ agentId, key: d.record_key })}>
                  <td className="max-w-[22rem] truncate">{d.title}</td>
                  <td className="tabular-nums">{num(d.questions)}</td>
                  <td className={cn("tabular-nums", d.down > 0 && "text-red-600 dark:text-red-400")}>{num(d.down)}</td>
                </tr>
              ))}
              {s.top_documents.length === 0 && (
                <tr><td colSpan={3} className="py-8 text-center text-muted-foreground">עדיין אין שאלות עם מקורות.</td></tr>
              )}
            </tbody>
          </table>
        </Panel>
        {agentId && <ChunkQualityPanel agentId={agentId} token={token} onOpen={key => setOpenDoc({ agentId, key })} />}
      </div>

      {openDoc && (
        <DocumentViewer agentId={openDoc.agentId} recordKey={openDoc.key} token={token} initialView="chunks"
          onClose={() => setOpenDoc(null)} />
      )}
    </div>
  )
}

/** Searchable, filterable log of questions; a row opens the full question. */
export function QuestionLog({ token, days, user, compact }: {
  token: string | undefined
  days: number
  user?: string
  compact?: boolean
}) {
  const [flag, setFlag] = useState<QuestionFlag | null>(null)
  const [q, setQ] = useState("")
  const [sort, setSort] = useState<QuestionQuery["sort"]>("recent")
  const [page, setPage] = useState(1)
  const [data, setData] = useState<QuestionPage | null>(null)
  const [error, setError] = useState("")
  const [open, setOpen] = useState<string | null>(null)
  const requestId = useRef(0)

  const load = useCallback(() => {
    const id = ++requestId.current
    getQuestions({ days, flag, q, sort, page, user }, token)
      .then(d => { if (id === requestId.current) setData(d) })
      .catch(e => setError(e.message))
  }, [days, flag, q, sort, page, user, token])

  useEffect(() => { load() }, [load])
  useEffect(() => { setPage(1) }, [days, flag, q, sort, user])

  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1

  return (
    <div className={cn(CARD, "overflow-hidden")}>
      <div className="flex flex-wrap items-center gap-1.5 border-b px-4 py-3">
        <p className="me-2 text-sm font-medium">יומן שאלות {data && <span className="text-muted-foreground">({num(data.total)})</span>}</p>
        {FLAGS.map(f => (
          <button key={f.label} className="chip" data-active={flag === f.id} onClick={() => setFlag(f.id)}>{f.label}</button>
        ))}
        <span className="flex-1" />
        {!compact && (
          <>
            <select className="h-8 rounded-lg border bg-background px-2 text-xs" value={sort}
              onChange={e => setSort(e.target.value as QuestionQuery["sort"])}>
              <option value="recent">החדשות ביותר</option>
              <option value="cost">היקרות ביותר</option>
              <option value="slow">האיטיות ביותר</option>
            </select>
            <Input className="h-8 w-44 text-xs" placeholder="חיפוש בשאלות" value={q} onChange={e => setQ(e.target.value)} />
            <Button size="sm" variant="outline" className="h-8 gap-1"
              onClick={() => downloadExport("questions", { days, flag }, token).catch(e => setError(e.message))}>
              <Download className="h-3.5 w-3.5" /> CSV
            </Button>
          </>
        )}
      </div>
      <ErrorNote message={error} />
      <div className="overflow-x-auto">
        <table className="data-table">
          <thead>
            <tr>
              <th>שאלה</th>
              {!user && <th>משתמש</th>}
              <th>מסלול</th>
              <th>קטעים</th>
              <th>זמן</th>
              <th>עלות</th>
              <th>דירוג</th>
              <th>מתי</th>
            </tr>
          </thead>
          <tbody>
            {(data?.questions ?? []).map(row => (
              <tr key={row.id} className="cursor-pointer" onClick={() => setOpen(row.id)}>
                <td className="max-w-[26rem]">
                  <p className="truncate">{row.query}</p>
                  {row.comment && <p className="truncate text-xs text-red-600 dark:text-red-400">״{row.comment}״</p>}
                  {row.error && <p className="truncate text-xs text-red-600 dark:text-red-400">{row.error}</p>}
                </td>
                {!user && <td className="max-w-[10rem] truncate text-xs" dir="ltr">{row.email}</td>}
                <td>
                  <div className="flex flex-wrap gap-1">
                    {row.route && <Pill className={ROUTE_LABEL[row.route].className}>{ROUTE_LABEL[row.route].label}</Pill>}
                    {!row.ok && <Pill className={TONE.red}>שגיאה</Pill>}
                    {!!row.no_answer && <Pill className={TONE.amber}>לא נמצא</Pill>}
                  </div>
                </td>
                <td className="tabular-nums">{num(row.retrieved)}</td>
                <td className="whitespace-nowrap tabular-nums text-xs">{seconds(row.latency_ms)}</td>
                <td className="tabular-nums text-xs">{usdPrecise(row.cost_usd)}</td>
                <td><RatingIcon rating={row.rating} /></td>
                <td className="whitespace-nowrap text-xs text-muted-foreground">{dateTime(row.ts)}</td>
              </tr>
            ))}
            {data && data.questions.length === 0 && (
              <tr><td colSpan={8} className="py-10 text-center text-sm text-muted-foreground">אין שאלות להצגה.</td></tr>
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
      <QuestionDialog questionId={open} token={token} onClose={() => setOpen(null)} />
    </div>
  )
}

function ChunkQualityPanel({ agentId, token, onOpen }: {
  agentId: string
  token: string | undefined
  onOpen: (key: string) => void
}) {
  const [data, setData] = useState<LibraryChunkQuality | null>(null)
  useEffect(() => { getChunkQuality(agentId, token).then(setData).catch(() => {}) }, [agentId, token])
  if (!data) return null
  return (
    <Panel title="איכות הקטעים באינדקס">
      {data.measured === 0 ? (
        <p className="text-sm text-muted-foreground">
          יוצג אחרי שמסמכים יאונדקסו. בינתיים אפשר לפתוח מסמך שפורסר ולבחור ״קטעים״ כדי לראות איך הוא יחולק.
        </p>
      ) : (
        <>
          <p className="text-sm">
            {num(data.chunks)} קטעים ב-{num(data.measured)} מסמכים · אורך ממוצע {num(Math.round(data.avg_chars ?? 0))} תווים ·{" "}
            <span className={data.with_problems ? "text-amber-600 dark:text-amber-400" : ""}>
              {pct(data.chunks ? data.with_problems / data.chunks : null)} עם בעיה
            </span>
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {Object.entries(data.flags).filter(([, n]) => n).map(([f, n]) => (
              <Pill key={f} className={CHUNK_FLAG[f as keyof typeof CHUNK_FLAG]?.className ?? TONE.neutral}>
                {CHUNK_FLAG[f as keyof typeof CHUNK_FLAG]?.label ?? f}: {num(n)}
              </Pill>
            ))}
          </div>
          {data.worst_documents.length > 0 && (
            <table className="data-table mt-3 text-xs">
              <thead><tr><th>מסמכים לבדיקה</th><th>קטעים</th><th>עם בעיה</th></tr></thead>
              <tbody>
                {data.worst_documents.slice(0, 8).map(d => (
                  <tr key={d.record_key} className="cursor-pointer" onClick={() => onOpen(d.record_key)}>
                    <td className="max-w-[18rem] truncate">{d.title}</td>
                    <td className="tabular-nums">{num(d.chunks)}</td>
                    <td className="tabular-nums">{num(d.with_problems)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </Panel>
  )
}
