"use client"

import { useEffect, useState } from "react"
import { getUsage, UsageSummary } from "@/lib/admin-api"
import { cn } from "@/lib/utils"
import { CARD, DailyBars, ErrorNote, monthProjection, num, StatCard, usd } from "./shared"

const RANGES = [7, 30, 90]

export function UsageTab({ token }: { token: string | undefined }) {
  const [days, setDays] = useState(30)
  const [data, setData] = useState<UsageSummary | null>(null)
  const [error, setError] = useState("")

  useEffect(() => {
    getUsage(days, token).then(setData).catch(e => setError(e.message))
  }, [days, token])

  if (error) return <ErrorNote message={error} />
  if (!data) return <p className="text-sm text-muted-foreground">טוען…</p>

  const rangeQuestions = data.daily.reduce((sum, d) => sum + d.questions, 0)
  const rangeCost = data.daily.reduce((sum, d) => sum + d.chat_cost + d.indexing_cost, 0)
  const rangeTokens = data.daily.reduce((sum, d) => sum + d.input_tokens + d.output_tokens, 0)

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="עלות החודש" value={usd(data.cost_month)}
          hint={`צפי לסוף החודש: ${usd(monthProjection(data.cost_month))}`} />
        <StatCard label="מתוכה אינדוקס מסמכים" value={usd(data.indexing_cost_month)} hint="עלות חד-פעמית לכל מסמך" />
        <StatCard label="שאלות החודש" value={num(data.questions_month)} hint={`${num(data.questions_today)} היום`} />
        <StatCard label="עלות ממוצעת לשאלה" value={usd(data.avg_cost_per_question)} hint="החודש, שיחות בלבד" />
      </div>

      <div className="flex flex-wrap items-center gap-2 py-1">
        <span className="text-sm text-muted-foreground">טווח:</span>
        {RANGES.map(r => (
          <button key={r} onClick={() => setDays(r)} className="chip" data-active={days === r}>
            {r} ימים
          </button>
        ))}
        <span className="ms-1 text-xs text-muted-foreground">
          {num(rangeQuestions)} שאלות · {usd(rangeCost)} · {num(Math.round(rangeTokens / 1000))}K טוקנים
        </span>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <div className={cn(CARD, "p-5")}>
          <p className="mb-4 text-[13px] text-muted-foreground">עלות יומית (שיחות + אינדוקס)</p>
          <DailyBars days={data.daily} pick={d => d.chat_cost + d.indexing_cost} format={usd}
            barClassName="bg-emerald-500/70 hover:bg-emerald-500" />
        </div>
        <div className={cn(CARD, "p-5")}>
          <p className="mb-4 text-[13px] text-muted-foreground">שאלות ביום</p>
          <DailyBars days={data.daily} pick={d => d.questions} format={n => `${n} שאלות`} />
        </div>
      </div>

      <div className={cn(CARD, "overflow-x-auto")}>
        <p className="border-b px-4 py-3 text-sm font-medium">המשתמשים היקרים ביותר החודש</p>
        <table className="data-table">
          <thead>
            <tr>
              <th>משתמש</th>
              <th>שאלות היום</th>
              <th>שאלות החודש</th>
              <th>עלות החודש</th>
              <th>עלות לשאלה</th>
            </tr>
          </thead>
          <tbody>
            {(data.top_users ?? []).map(u => (
              <tr key={u.sub}>
                <td>
                  {u.name || "—"} <span dir="ltr" className="text-xs text-muted-foreground">{u.email}</span>
                </td>
                <td className="tabular-nums">{num(u.questions_today)}</td>
                <td className="tabular-nums">{num(u.questions_month)}</td>
                <td className="tabular-nums">{usd(u.cost_month)}</td>
                <td className="tabular-nums">{usd(u.questions_month ? u.cost_month / u.questions_month : 0)}</td>
              </tr>
            ))}
            {!(data.top_users ?? []).length && (
              <tr><td colSpan={5} className="py-12 text-center text-sm text-muted-foreground">עדיין אין שימוש החודש.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <p className="text-xs text-muted-foreground">
        העלויות הן הערכה: מספר הטוקנים שהמודל דיווח כפול המחירים שבהגדרות. החיוב בפועל מופיע ב-Google AI Studio.
      </p>
    </div>
  )
}
