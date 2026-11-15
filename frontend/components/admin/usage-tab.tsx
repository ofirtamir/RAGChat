"use client"

import { useEffect, useState } from "react"
import { getUsage, UsageSummary } from "@/lib/admin-api"
import { cn } from "@/lib/utils"
import { DailyBars, ErrorNote, monthProjection, num, StatCard, usd } from "./shared"

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
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="עלות החודש" value={usd(data.cost_month)}
          hint={`צפי לסוף החודש: ${usd(monthProjection(data.cost_month))}`} />
        <StatCard label="מתוכה אינדוקס מסמכים" value={usd(data.indexing_cost_month)} hint="עלות חד-פעמית לכל מסמך" />
        <StatCard label="שאלות החודש" value={num(data.questions_month)} hint={`${num(data.questions_today)} היום`} />
        <StatCard label="עלות ממוצעת לשאלה" value={usd(data.avg_cost_per_question)} hint="החודש, שיחות בלבד" />
      </div>

      <div className="flex items-center gap-2">
        <span className="text-sm text-muted-foreground">טווח:</span>
        {RANGES.map(r => (
          <button key={r} onClick={() => setDays(r)}
            className={cn("rounded-full border px-3 py-1 text-xs", days === r && "border-sky-500 bg-sky-50 dark:bg-sky-950")}>
            {r} ימים
          </button>
        ))}
        <span className="text-xs text-muted-foreground">
          {num(rangeQuestions)} שאלות · {usd(rangeCost)} · {num(Math.round(rangeTokens / 1000))}K טוקנים
        </span>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <div className="rounded-xl border bg-card p-4">
          <p className="mb-3 text-xs text-muted-foreground">עלות יומית (שיחות + אינדוקס)</p>
          <DailyBars days={data.daily} pick={d => d.chat_cost + d.indexing_cost} format={usd}
            barClassName="bg-emerald-500/80 hover:bg-emerald-500" />
        </div>
        <div className="rounded-xl border bg-card p-4">
          <p className="mb-3 text-xs text-muted-foreground">שאלות ביום</p>
          <DailyBars days={data.daily} pick={d => d.questions} format={n => `${n} שאלות`} />
        </div>
      </div>

      <div className="overflow-x-auto rounded-xl border bg-card">
        <p className="border-b px-3 py-2 text-xs text-muted-foreground">המשתמשים היקרים ביותר החודש</p>
        <table className="w-full text-sm">
          <thead className="border-b bg-muted/40 text-xs text-muted-foreground">
            <tr>
              <th className="p-2 text-right font-medium">משתמש</th>
              <th className="p-2 text-right font-medium">שאלות היום</th>
              <th className="p-2 text-right font-medium">שאלות החודש</th>
              <th className="p-2 text-right font-medium">עלות החודש</th>
              <th className="p-2 text-right font-medium">עלות לשאלה</th>
            </tr>
          </thead>
          <tbody>
            {(data.top_users ?? []).map(u => (
              <tr key={u.sub} className="border-b last:border-0">
                <td className="p-2">
                  {u.name || "—"} <span dir="ltr" className="text-xs text-muted-foreground">{u.email}</span>
                </td>
                <td className="p-2 tabular-nums">{num(u.questions_today)}</td>
                <td className="p-2 tabular-nums">{num(u.questions_month)}</td>
                <td className="p-2 tabular-nums">{usd(u.cost_month)}</td>
                <td className="p-2 tabular-nums">{usd(u.questions_month ? u.cost_month / u.questions_month : 0)}</td>
              </tr>
            ))}
            {!(data.top_users ?? []).length && (
              <tr><td colSpan={5} className="p-8 text-center text-sm text-muted-foreground">עדיין אין שימוש החודש.</td></tr>
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
