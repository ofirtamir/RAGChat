"use client"

import { useEffect, useMemo, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { ChunkFlag, ChunkRow, DocumentChunks, getDocumentChunks } from "@/lib/admin-insights-api"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"
import { ErrorNote, num, Pill, TONE } from "./shared"

export const CHUNK_FLAG: Record<ChunkFlag, { label: string; hint: string; className: string; problem: boolean }> = {
  tiny:       { label: "קצר מדי",     hint: "פחות מ-150 תווים — חיפוש שמוצא אותו לא מוסר למודל כמעט כלום", className: TONE.amber, problem: true },
  long:       { label: "ארוך מאוד",   hint: "יותר מפי 2 מהגודל המתוכנן — בדרך כלל טבלה גדולה שנשמרה שלמה", className: TONE.orange, problem: true },
  no_section: { label: "ללא סעיף",    hint: "לא נמצא תחת סעיף ממוספר, ולכן אין לו כותרת סעיף", className: TONE.violet, problem: true },
  garbled:    { label: "טקסט משובש",  hint: "תווים שנראים כמו עברית שפוענחה בקידוד שגוי", className: TONE.red, problem: true },
  duplicate:  { label: "כפול",         hint: "אותו טקסט מופיע בקטע נוסף במסמך", className: TONE.red, problem: true },
  table:      { label: "טבלה",         hint: "הקטע מכיל טבלה", className: TONE.sky, problem: false },
}

const BUCKETS = ["עד 250", "250–500", "500–750", "750–1,000", "1,000–1,500", "1,500+"]

/** Every chunk of one document as search sees it, with quality flags and a summary. */
export function ChunkViewer({ agentId, recordKey, indexed, token, raw }: {
  agentId: string
  recordKey: string
  indexed: boolean
  token: string | undefined
  raw: boolean
}) {
  const [preview, setPreview] = useState(!indexed)
  const [data, setData] = useState<DocumentChunks | null>(null)
  const [error, setError] = useState("")
  const [flag, setFlag] = useState<ChunkFlag | "problems" | null>(null)
  const [section, setSection] = useState("")
  const [q, setQ] = useState("")

  useEffect(() => { setPreview(!indexed) }, [indexed, recordKey])
  useEffect(() => {
    setData(null)
    setError("")
    getDocumentChunks(agentId, recordKey, preview, token).then(setData).catch(e => setError(e.message))
  }, [agentId, recordKey, preview, token])

  const sections = useMemo(
    () => Array.from(new Set((data?.chunks ?? []).map(c => c.section || "ללא סעיף"))),
    [data],
  )
  const needle = q.trim()
  const visible = (data?.chunks ?? []).filter(c =>
    (!flag || (flag === "problems" ? c.flags.some(f => CHUNK_FLAG[f].problem) : c.flags.includes(flag))) &&
    (!section || (c.section || "ללא סעיף") === section) &&
    (!needle || c.text.includes(needle)))

  if (error) return <ErrorNote message={error} />
  if (!data) return <p className="py-10 text-center text-sm text-muted-foreground">טוען קטעים…</p>
  if (data.source === "none" || !data.summary) {
    return <p className="py-10 text-center text-sm text-muted-foreground">אין טקסט מפורסר — אין מה לחלק לקטעים.</p>
  }

  const s = data.summary
  const maxBucket = Math.max(...s.size_buckets, 1)

  return (
    <div className="space-y-3">
      <div className="rounded-xl border bg-background p-4">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Pill className={data.source === "index" ? TONE.emerald : TONE.sky}>
            {data.source === "index" ? "כפי שנשמר באינדקס" : "תצוגה מקדימה — כך המסמך יחולק (בחינם, בלי אינדוקס)"}
          </Pill>
          {indexed && (
            <label className="flex items-center gap-1.5 text-muted-foreground">
              <input type="checkbox" checked={preview} onChange={e => setPreview(e.target.checked)} />
              הצג חלוקה לפי הקוד הנוכחי (להשוואה)
            </label>
          )}
        </div>
        <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1.5 text-[13px] sm:grid-cols-4">
          <Stat label="קטעים" value={num(s.chunks)} />
          <Stat label="סעיפים" value={num(s.sections)} />
          <Stat label="אורך חציוני" value={`${num(s.median)} תווים`} />
          <Stat label="טווח אורכים" value={`${num(s.min)}–${num(s.max)}`} />
          <Stat label="טוקנים (הערכה)" value={num(s.tokens_est)} />
          <Stat label="קטעים עם בעיה" value={`${num(s.with_problems)} (${s.chunks ? Math.round(s.with_problems / s.chunks * 100) : 0}%)`}
            warn={s.with_problems > 0} />
        </div>
        <div className="mt-4">
          <p className="mb-1.5 text-xs text-muted-foreground">התפלגות אורכי קטעים (תווים)</p>
          <div className="flex h-14 items-end gap-1" dir="ltr">
            {s.size_buckets.map((n, i) => (
              <div key={i} className="flex h-full flex-1 flex-col items-center justify-end gap-1" title={`${BUCKETS[i]}: ${n}`}>
                <div className="w-full rounded-t bg-foreground/60" style={{ height: `${(n / maxBucket) * 100}%`, minHeight: n ? 3 : 0 }} />
              </div>
            ))}
          </div>
          <div className="mt-1 flex gap-1 text-[10px] text-muted-foreground" dir="ltr">
            {BUCKETS.map(b => <span key={b} className="flex-1 text-center">{b}</span>)}
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <button className="chip" data-active={!flag} onClick={() => setFlag(null)}>הכל: {num(s.chunks)}</button>
        {s.with_problems > 0 && (
          <button className="chip" data-active={flag === "problems"} onClick={() => setFlag("problems")}>
            עם בעיה: {num(s.with_problems)}
          </button>
        )}
        {(Object.keys(CHUNK_FLAG) as ChunkFlag[]).filter(f => s.flags[f]).map(f => (
          <button key={f} className="chip" data-active={flag === f} title={CHUNK_FLAG[f].hint}
            onClick={() => setFlag(flag === f ? null : f)}>
            {CHUNK_FLAG[f].label}: {num(s.flags[f])}
          </button>
        ))}
        <span className="flex-1" />
        <select className="h-8 max-w-[14rem] rounded-lg border bg-background px-2 text-xs" value={section}
          onChange={e => setSection(e.target.value)}>
          <option value="">כל הסעיפים</option>
          {sections.map(sec => <option key={sec} value={sec}>{sec}</option>)}
        </select>
        <Input className="h-8 w-40 text-xs" placeholder="חיפוש בקטעים" value={q} onChange={e => setQ(e.target.value)} />
      </div>

      {visible.length === 0 && <p className="py-6 text-center text-sm text-muted-foreground">אין קטעים להצגה.</p>}
      {visible.map(c => <ChunkCard key={c.chunk_index} chunk={c} raw={raw} highlight={needle} />)}
    </div>
  )
}

function Stat({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <p className="flex justify-between gap-2 border-b border-dashed py-1">
      <span className="text-muted-foreground">{label}</span>
      <span className={cn("tabular-nums font-medium", warn && "text-amber-600 dark:text-amber-400")}>{value}</span>
    </p>
  )
}

export function ChunkCard({ chunk, raw, highlight, extra }: {
  chunk: Pick<ChunkRow, "text" | "section" | "page" | "chunk_index"> & Partial<Pick<ChunkRow, "chars" | "tokens_est" | "flags">>
  raw: boolean
  highlight?: string
  extra?: React.ReactNode
}) {
  const problem = chunk.flags?.some(f => CHUNK_FLAG[f].problem)
  const body = chunk.text.replace(/^\[[^\]\n]{1,120}\]\n/, "")
  return (
    <section className={cn("rounded-xl border bg-background p-4", problem && "border-amber-400 dark:border-amber-400/50")}>
      <div className="mb-2 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
        <span className="rounded-md bg-muted px-1.5 py-0.5 font-mono tabular-nums text-foreground">#{chunk.chunk_index}</span>
        <span className="font-medium text-foreground">{chunk.section || "ללא סעיף"}</span>
        <span>· עמוד {chunk.page + 1}</span>
        {chunk.chars != null && <span>· {num(chunk.chars)} תווים · ~{num(chunk.tokens_est)} טוקנים</span>}
        {chunk.flags?.map(f => (
          <Pill key={f} className={CHUNK_FLAG[f].className}><span title={CHUNK_FLAG[f].hint}>{CHUNK_FLAG[f].label}</span></Pill>
        ))}
        {extra}
      </div>
      {raw ? (
        <pre dir="rtl" className="whitespace-pre-wrap break-words text-xs leading-relaxed">
          {highlight ? markText(body, highlight) : body}
        </pre>
      ) : (
        <div className="prose prose-sm max-w-none overflow-x-auto dark:prose-invert prose-table:text-xs">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
        </div>
      )}
    </section>
  )
}

function markText(text: string, needle: string) {
  const parts = text.split(needle)
  return parts.flatMap((part, i) => i === 0 ? [part] : [<mark key={i} className="rounded bg-amber-200 dark:bg-amber-400/40">{needle}</mark>, part])
}
