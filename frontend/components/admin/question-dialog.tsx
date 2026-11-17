"use client"

import { useEffect, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { ThumbsDown, ThumbsUp } from "lucide-react"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { getQuestion, QuestionDetail, QuestionRoute } from "@/lib/admin-insights-api"
import { cn } from "@/lib/utils"
import { DocumentViewer } from "./document-viewer"
import { dateTime, ErrorNote, num, Pill, seconds, TONE, usdPrecise } from "./shared"

export const ROUTE_LABEL: Record<QuestionRoute, { label: string; className: string }> = {
  retrieval: { label: "אחזור קטעים", className: TONE.sky },
  full_doc:  { label: "מסמך מלא",    className: TONE.violet },
  chitchat:  { label: "שיחה חופשית", className: TONE.neutral },
  no_docs:   { label: "לא נמצאו מקורות", className: TONE.orange },
}

export function RatingIcon({ rating }: { rating: number | null }) {
  if (rating === 1) return <ThumbsUp className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
  if (rating === -1) return <ThumbsDown className="h-3.5 w-3.5 text-red-600 dark:text-red-400" />
  return null
}

/** Everything about one question: how it was searched, which chunks the answer used, and the answer. */
export function QuestionDialog({ questionId, token, onClose }: {
  questionId: string | null
  token: string | undefined
  onClose: () => void
}) {
  const [q, setQ] = useState<QuestionDetail | null>(null)
  const [error, setError] = useState("")
  const [openDoc, setOpenDoc] = useState<{ agentId: string; key: string } | null>(null)

  useEffect(() => {
    setQ(null)
    setError("")
    if (questionId) getQuestion(questionId, token).then(setQ).catch(e => setError(e.message))
  }, [questionId, token])

  const filters = Object.entries(q?.filters ?? {})

  return (
    <>
      <Dialog open={!!questionId} onOpenChange={open => !open && onClose()}>
        <DialogContent dir="rtl" className="flex h-[92vh] max-w-4xl flex-col gap-4 text-right">
          <DialogHeader className="space-y-2 pe-8 text-right sm:text-right">
            <DialogTitle className="text-lg font-semibold leading-snug">{q?.query ?? "טוען…"}</DialogTitle>
            <DialogDescription asChild>
              <div className="flex flex-wrap items-center gap-2 text-xs">
                {q && (
                  <>
                    <span>{q.name || "—"} <span dir="ltr">{q.email}</span></span>
                    <span>· {dateTime(q.ts)}</span>
                    {q.route && <Pill className={ROUTE_LABEL[q.route].className}>{ROUTE_LABEL[q.route].label}</Pill>}
                    {!q.ok && <Pill className={TONE.red}>שגיאה</Pill>}
                    {!!q.no_answer && <Pill className={TONE.amber}>לא נמצאה תשובה במסמכים</Pill>}
                  </>
                )}
              </div>
            </DialogDescription>
          </DialogHeader>
          <ErrorNote message={error} />

          {q && (
            <div className="scrollbar-thin min-h-0 flex-1 space-y-4 overflow-y-auto">
              {q.rating != null && (
                <div className={cn("flex items-start gap-2 rounded-xl border p-3 text-sm",
                  q.rating === -1 ? "border-red-200 bg-red-50 dark:border-red-400/20 dark:bg-red-400/10"
                    : "border-emerald-200 bg-emerald-50 dark:border-emerald-400/20 dark:bg-emerald-400/10")}>
                  <RatingIcon rating={q.rating} />
                  <div>
                    <p className="font-medium">{q.rating === 1 ? "המשתמש סימן שהתשובה עזרה" : "המשתמש סימן שהתשובה לא עזרה"}</p>
                    {q.comment && <p className="mt-1 whitespace-pre-wrap">{q.comment}</p>}
                  </div>
                </div>
              )}
              {q.error && <ErrorNote message={q.error} />}

              <div className="grid grid-cols-2 gap-x-6 gap-y-1 rounded-xl border p-3 text-[13px] sm:grid-cols-4">
                <Fact label="זמן כולל" value={seconds(q.latency_ms)} />
                <Fact label="עד המילה הראשונה" value={seconds(q.first_token_ms)} />
                <Fact label="עלות" value={usdPrecise(q.cost_usd)} />
                <Fact label="קריאות למודל" value={num(q.llm_calls)} />
                <Fact label="טוקנים נכנסים" value={num(q.input_tokens)} />
                <Fact label="טוקנים יוצאים" value={num(q.output_tokens)} />
                <Fact label="קטעים שנשלחו למודל" value={num(q.retrieved)} />
                <Fact label="מודלים" value={q.models || "—"} />
              </div>

              {(q.search_queries.length > 0 || filters.length > 0) && (
                <div className="space-y-1.5 text-xs">
                  {q.search_queries.length > 0 && (
                    <p className="flex flex-wrap items-center gap-1.5">
                      <span className="text-muted-foreground">חיפשנו:</span>
                      {q.search_queries.map((s, i) => <code key={i} className="rounded-md bg-muted px-2 py-0.5">{s}</code>)}
                    </p>
                  )}
                  {filters.length > 0 && (
                    <p className="flex flex-wrap items-center gap-1.5">
                      <span className="text-muted-foreground">מסננים:</span>
                      {filters.map(([k, v]) => (
                        <code key={k} className="rounded-md bg-muted px-2 py-0.5">
                          {k}: {typeof v === "string" ? v : `${v.from ?? ""}–${v.to ?? ""}`}
                        </code>
                      ))}
                    </p>
                  )}
                </div>
              )}

              {q.retrieval.length > 0 && (
                <div className="rounded-xl border">
                  <p className="border-b px-3 py-2 text-xs font-medium text-muted-foreground">
                    הקטעים שהתשובה התבססה עליהם (לפי הסדר שנשלח למודל; [1] בתשובה = השורה הראשונה)
                  </p>
                  <table className="data-table text-xs">
                    <thead>
                      <tr><th>#</th><th>מסמך</th><th>סעיף</th><th>עמוד</th><th>ציון חיפוש</th><th>ציון רלוונטיות</th></tr>
                    </thead>
                    <tbody>
                      {q.retrieval.map((c, i) => (
                        <tr key={i} className={cn(c.record_key && q.agent_id && "cursor-pointer")}
                          onClick={() => c.record_key && q.agent_id && setOpenDoc({ agentId: q.agent_id, key: c.record_key })}>
                          <td className="tabular-nums">{i + 1}</td>
                          <td className="max-w-[18rem] truncate">{c.source}</td>
                          <td className="max-w-[10rem] truncate">{c.section || "—"}</td>
                          <td className="tabular-nums">{c.page != null ? c.page + 1 : "—"}</td>
                          <td className="tabular-nums">{c.score != null ? c.score.toFixed(4) : "—"}</td>
                          <td className="tabular-nums">{c.rerank != null ? `${c.rerank}/10` : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <div className="rounded-xl border bg-muted/40 p-4">
                <p className="mb-2 text-xs font-medium text-muted-foreground">התשובה שקיבל המשתמש</p>
                {q.answer ? (
                  <div className="prose prose-sm max-w-none dark:prose-invert prose-table:text-xs">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{q.answer}</ReactMarkdown>
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">אין תשובה שמורה.</p>
                )}
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
      {openDoc && (
        <DocumentViewer agentId={openDoc.agentId} recordKey={openDoc.key} token={token}
          initialView="chunks" onClose={() => setOpenDoc(null)} />
      )}
    </>
  )
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <p className="flex justify-between gap-2 py-0.5">
      <span className="text-muted-foreground">{label}</span>
      <span className="truncate tabular-nums font-medium">{value}</span>
    </p>
  )
}
