"use client"

import { useEffect, useState } from "react"
import { Input } from "@/components/ui/input"
import { Costs, Engagement, getCosts, getEngagement, Segment } from "@/lib/admin-insights-api"
import { cn } from "@/lib/utils"
import { QuestionDialog, ROUTE_LABEL } from "./question-dialog"
import { DailyBars, dateTime, ErrorNote, num, Panel, pct, shortDate, StatCard, usd, usdPrecise } from "./shared"

const SEGMENTS: Record<Segment, { label: string; hint: string }> = {
  power:   { label: "משתמשי כוח", hint: "20+ שאלות ב-30 יום" },
  regular: { label: "פעילים", hint: "1–19 שאלות ב-30 יום" },
  dormant: { label: "רדומים", hint: "שאלו בעבר, לא ב-30 יום — כדאי לפנות אליהם" },
  never:   { label: "אושרו ולא שאלו", hint: "קיבלו גישה ולא התחילו — בעיית קליטה" },
}

/** Who uses the product and whether they come back. */
export function EngagementPanel({ token, onOpenUser }: { token: string | undefined; onOpenUser?: (sub: string) => void }) {
  const [data, setData] = useState<Engagement | null>(null)
  const [error, setError] = useState("")
  const [segment, setSegment] = useState<Segment>("dormant")

  useEffect(() => { getEngagement(token).then(setData).catch(e => setError(e.message)) }, [token])
  if (error) return <ErrorNote message={error} />
  if (!data) return null
  const maxCohort = Math.max(...data.cohorts.map(c => c.retention.length), 1)

  return (
    <div className="space-y-3">
      <h2 className="pt-2 text-base font-semibold">מעורבות משתמשים</h2>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="פעילים היום / השבוע / החודש" value={`${num(data.dau)} / ${num(data.wau)} / ${num(data.mau)}`}
          hint={`מתוך ${num(data.approved_users)} משתמשים מאושרים`} />
        <StatCard label="דביקות (פעילים ביום ÷ בחודש)" value={pct(data.stickiness)}
          hint="20%+ = המוצר הוא הרגל; מתחת ל-10% = שימוש מזדמן" />
        <StatCard label="אחוז מהנרשמים ששאלו" value={pct(data.activation)} hint="מכל מי שנכנס אי פעם" />
        <StatCard label="רדומים" value={num(data.segments.dormant.count)}
          tone={data.segments.dormant.count > 0 ? "warn" : undefined}
          hint={`${num(data.segments.never.count)} אושרו ועוד לא שאלו`} />
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="משתמשים פעילים ביום (30 יום)">
          <DailyBars days={data.daily_active} pick={d => d.users} format={n => `${n} משתמשים`} height="h-28"
            barClassName="bg-sky-500/70 hover:bg-sky-500" />
        </Panel>
        <Panel title="נרשמים חדשים לשבוע (כהה = שאלו לפחות פעם אחת)">
          <div className="flex h-28 items-end gap-2" dir="ltr">
            {data.signups.map(w => {
              const max = Math.max(...data.signups.map(s => s.signed_up), 1)
              return (
                <div key={w.week} className="flex h-full flex-1 flex-col justify-end" title={`${shortDate(w.week)}: ${w.signed_up} נרשמו, ${w.activated} שאלו`}>
                  <div className="relative w-full rounded-t bg-foreground/20" style={{ height: `${(w.signed_up / max) * 100}%` }}>
                    <div className="absolute inset-x-0 bottom-0 rounded-t bg-foreground/70"
                      style={{ height: w.signed_up ? `${(w.activated / w.signed_up) * 100}%` : 0 }} />
                  </div>
                </div>
              )
            })}
          </div>
        </Panel>
      </div>

      <Panel title="שימור: מכל מי ששאל לראשונה בשבוע מסוים — כמה חזרו לשאול בשבועות הבאים">
        <div className="overflow-x-auto">
          <table className="data-table text-xs">
            <thead>
              <tr>
                <th>שבוע ראשון</th><th>משתמשים</th>
                {Array.from({ length: maxCohort }, (_, k) => <th key={k}>{k === 0 ? "שבוע 0" : `+${k}`}</th>)}
              </tr>
            </thead>
            <tbody>
              {data.cohorts.filter(c => c.size > 0).map(c => (
                <tr key={c.week}>
                  <td className="whitespace-nowrap">{shortDate(c.week)}</td>
                  <td className="tabular-nums">{num(c.size)}</td>
                  {Array.from({ length: maxCohort }, (_, k) => {
                    const v = c.retention[k]
                    return (
                      <td key={k} className="tabular-nums"
                        style={v != null ? { backgroundColor: `rgb(16 185 129 / ${0.08 + v * 0.5})` } : undefined}>
                        {v != null ? pct(v) : ""}
                      </td>
                    )
                  })}
                </tr>
              ))}
              {!data.cohorts.some(c => c.size > 0) && (
                <tr><td colSpan={3} className="py-6 text-center text-muted-foreground">עדיין אין מספיק נתונים.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="פילוח המשתמשים המאושרים (30 יום)">
        <div className="mb-3 flex flex-wrap gap-1.5">
          {(Object.keys(SEGMENTS) as Segment[]).map(s => (
            <button key={s} className="chip" data-active={segment === s} onClick={() => setSegment(s)} title={SEGMENTS[s].hint}>
              {SEGMENTS[s].label}: {num(data.segments[s].count)}
            </button>
          ))}
        </div>
        <p className="mb-2 text-xs text-muted-foreground">{SEGMENTS[segment].hint}</p>
        <div className="max-h-72 overflow-y-auto">
          <table className="data-table text-xs">
            <thead><tr><th>משתמש</th><th>שאלות ב-30 יום</th><th>שאלה אחרונה</th><th>הצטרף</th></tr></thead>
            <tbody>
              {data.segments[segment].users.map(u => (
                <tr key={u.sub} className={cn(onOpenUser && "cursor-pointer")} onClick={() => onOpenUser?.(u.sub)}>
                  <td>{u.name || "—"} <span dir="ltr" className="text-muted-foreground">{u.email}</span></td>
                  <td className="tabular-nums">{num(u.questions_30d)}</td>
                  <td>{shortDate(u.last_question)}</td>
                  <td>{shortDate(u.created_at)}</td>
                </tr>
              ))}
              {data.segments[segment].users.length === 0 && (
                <tr><td colSpan={4} className="py-6 text-center text-muted-foreground">אין משתמשים בקבוצה הזו.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  )
}

/** Where the money goes, and what N users would cost. */
export function CostPanel({ token, days }: { token: string | undefined; days: number }) {
  const [data, setData] = useState<Costs | null>(null)
  const [error, setError] = useState("")
  const [users, setUsers] = useState(200)
  const [open, setOpen] = useState<string | null>(null)

  useEffect(() => { getCosts(days, token).then(setData).catch(e => setError(e.message)) }, [days, token])
  if (error) return <ErrorNote message={error} />
  if (!data) return null
  const u = data.unit
  const perUser = u.chat_cost_per_active_user_month
  const share = u.active_share ?? 1
  const projected = perUser != null ? perUser * users * share : null
  const total = data.by_kind.reduce((s, k) => s + k.cost, 0)

  return (
    <div className="space-y-3">
      <h2 className="pt-2 text-base font-semibold">עלויות לעומק ({days} ימים)</h2>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="עלות שיחות למשתמש פעיל בחודש" value={usdPrecise(perUser)}
          hint={`90% מהמשתמשים עד ${usdPrecise(u.user_cost_p90_month)}`} />
        <StatCard label="שאלות למשתמש פעיל בחודש" value={u.questions_per_active_user_month != null ? u.questions_per_active_user_month.toFixed(1) : "—"}
          hint={`${num(u.active_users)} פעילים מתוך ${num(u.approved_users)} (${pct(u.active_share)})`} />
        <StatCard label="עלות לשאלה: חציון / 90%" value={`${usdPrecise(data.per_question.p50)} / ${usdPrecise(data.per_question.p90)}`}
          hint={`יקרה ביותר ${usdPrecise(data.per_question.max)} · ממוצע ${num(Math.round(data.per_question.avg_input_tokens ?? 0))} טוקנים נכנסים`} />
        <div className="rounded-2xl border bg-card p-5">
          <p className="text-[13px] text-muted-foreground">צפי חודשי לפי מספר משתמשים</p>
          <div className="mt-1.5 flex items-center gap-2">
            <Input type="number" min={1} className="h-8 w-20" value={users} onChange={e => setUsers(Math.max(1, Number(e.target.value) || 1))} />
            <span className="text-sm text-muted-foreground">משתמשים ≈</span>
            <span className="text-[22px] font-semibold tabular-nums">{projected != null ? usd(projected) : "—"}</span>
          </div>
          <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
            לפי העלות למשתמש פעיל ושיעור הפעילים הנוכחי; בלי אינדוקס מסמכים.
          </p>
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="לפי סוג ולפי מודל">
          <div className="space-y-2">
            {data.by_kind.map(k => (
              <div key={k.kind} className="flex items-center gap-3 text-sm">
                <span className="w-24 shrink-0 text-muted-foreground">{KIND[k.kind] ?? k.kind}</span>
                <div className="h-2 flex-1 rounded-full bg-muted">
                  <div className="h-2 rounded-full bg-emerald-500/70" style={{ width: total ? `${(k.cost / total) * 100}%` : 0 }} />
                </div>
                <span className="w-20 text-left tabular-nums">{usdPrecise(k.cost)}</span>
              </div>
            ))}
          </div>
          <table className="data-table mt-4 text-xs">
            <thead><tr><th>מודל</th><th>סוג</th><th>קריאות</th><th>עלות</th></tr></thead>
            <tbody>
              {data.by_model.slice(0, 8).map((m, i) => (
                <tr key={i}>
                  <td dir="ltr" className="max-w-[14rem] truncate text-right">{m.model}</td>
                  <td>{KIND[m.kind] ?? m.kind}</td>
                  <td className="tabular-nums">{num(m.events)}</td>
                  <td className="tabular-nums">{usdPrecise(m.cost)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
        <Panel title="עלות ממוצעת לפי מסלול התשובה">
          <table className="data-table text-xs">
            <thead><tr><th>מסלול</th><th>שאלות</th><th>עלות ממוצעת</th><th>טוקנים נכנסים</th><th>יוצאים</th></tr></thead>
            <tbody>
              {data.by_route.map(r => (
                <tr key={r.route}>
                  <td>{ROUTE_LABEL[r.route as keyof typeof ROUTE_LABEL]?.label ?? "שגיאה"}</td>
                  <td className="tabular-nums">{num(r.questions)}</td>
                  <td className="tabular-nums">{usdPrecise(r.avg_cost)}</td>
                  <td className="tabular-nums">{num(Math.round(r.avg_input))}</td>
                  <td className="tabular-nums">{num(Math.round(r.avg_output))}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-3 text-xs text-muted-foreground">
            ״מסמך מלא״ שולח את כל המסמך למודל ולכן יקר בהרבה — אם הוא נפוץ ויקר, שווה לבדוק מתי הוא מופעל.
          </p>
        </Panel>
      </div>

      <Panel title="השאלות היקרות ביותר">
        <table className="data-table text-xs">
          <thead><tr><th>שאלה</th><th>משתמש</th><th>מסלול</th><th>טוקנים</th><th>עלות</th><th>מתי</th></tr></thead>
          <tbody>
            {data.expensive_questions.map(q => (
              <tr key={q.id} className="cursor-pointer" onClick={() => setOpen(q.id)}>
                <td className="max-w-[22rem] truncate">{q.query}</td>
                <td dir="ltr" className="max-w-[10rem] truncate text-right">{q.email}</td>
                <td>{ROUTE_LABEL[q.route as keyof typeof ROUTE_LABEL]?.label ?? "—"}</td>
                <td className="tabular-nums">{num(q.input_tokens + q.output_tokens)}</td>
                <td className="tabular-nums">{usdPrecise(q.cost_usd)}</td>
                <td className="whitespace-nowrap">{dateTime(q.ts)}</td>
              </tr>
            ))}
            {data.expensive_questions.length === 0 && (
              <tr><td colSpan={6} className="py-6 text-center text-muted-foreground">עדיין אין שאלות ביומן.</td></tr>
            )}
          </tbody>
        </table>
      </Panel>
      <QuestionDialog questionId={open} token={token} onClose={() => setOpen(null)} />
    </div>
  )
}

const KIND: Record<string, string> = { chat: "שיחות", indexing: "אינדוקס", parsing: "פרסור" }
