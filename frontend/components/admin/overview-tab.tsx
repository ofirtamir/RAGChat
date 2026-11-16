"use client"

import { useCallback, useEffect, useState } from "react"
import { getOverview, Overview } from "@/lib/admin-api"
import { cn } from "@/lib/utils"
import {
  CARD, DailyBars, DOC_STATUS, DOC_STATUS_ORDER, ErrorNote, IndexCostSummary, monthProjection, num,
  Pill, relativeTime, StatCard, TONE, usd,
} from "./shared"

export function OverviewTab({ token, onNavigate }: {
  token: string | undefined
  onNavigate: (tab: "library" | "users" | "usage" | "settings") => void
}) {
  const [data, setData] = useState<Overview | null>(null)
  const [error, setError] = useState("")

  const load = useCallback(() => {
    getOverview(token).then(setData).catch(e => setError(e.message))
  }, [token])

  useEffect(() => {
    load()
    const timer = setInterval(load, 30000)
    return () => clearInterval(timer)
  }, [load])

  if (error) return <ErrorNote message={error} />
  if (!data) return <p className="text-sm text-muted-foreground">טוען…</p>

  const { usage, users, settings, libraries, worker } = data
  const projection = monthProjection(usage.cost_month)
  const budget = settings.monthly_budget_usd
  const overBudget = budget > 0 && projection > budget

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          label="שאלות היום"
          value={num(usage.questions_today)}
          hint={`${num(users.active_today)} משתמשים פעילים`}
        />
        <StatCard label="עלות היום" value={usd(usage.cost_today)} hint="הערכה לפי טוקנים" />
        <StatCard
          label="עלות החודש"
          value={usd(usage.cost_month)}
          tone={overBudget ? "warn" : undefined}
          hint={
            <>
              צפי לסוף החודש: {usd(projection)}
              {budget > 0 && ` מתוך תקרה של ${usd(budget)}`}
            </>
          }
        />
        <StatCard
          label="עלות ממוצעת לשאלה"
          value={usd(usage.avg_cost_per_question)}
          hint={`${num(usage.questions_month)} שאלות החודש`}
        />
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <button
          onClick={() => onNavigate("users")}
          className={cn(CARD, "flex flex-col items-start p-5 text-right transition-colors hover:bg-muted/40")}
        >
          <p className="text-[13px] text-muted-foreground">משתמשים</p>
          <p className="mt-1.5 text-[26px] font-semibold leading-tight tracking-tight tabular-nums">{num(users.total)}</p>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {users.pending > 0 && (
              <Pill className={TONE.amber}>
                {users.pending} ממתינים לאישור
              </Pill>
            )}
            <Pill className={TONE.emerald}>
              {users.approved} מאושרים
            </Pill>
            {users.blocked > 0 && (
              <Pill className={TONE.red}>
                {users.blocked} חסומים
              </Pill>
            )}
          </div>
        </button>

        <div className={cn(CARD, "p-5")}>
          <div className="flex items-center justify-between">
            <p className="text-[13px] text-muted-foreground">שאלות ב-14 הימים האחרונים</p>
            <button onClick={() => onNavigate("usage")} className="rounded-full px-2 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground">
              לפירוט
            </button>
          </div>
          <div className="mt-4">
            <DailyBars days={usage.daily} pick={d => d.questions} format={n => `${n} שאלות`} />
          </div>
        </div>
      </div>

      {libraries.map(lib => lib.index_cost.documents > 0 && (
        <IndexCostSummary key={`cost-${lib.agent_id}`} cost={lib.index_cost}
          catalogTotal={lib.catalog_total} indexed={lib.counts.indexed} />
      ))}

      {libraries.map(lib => (
        <button
          key={lib.agent_id}
          onClick={() => onNavigate("library")}
          className={cn(CARD, "block w-full p-5 text-right transition-colors hover:bg-muted/40")}
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="font-medium">{lib.name}</p>
            <Pill className={worker.online ? TONE.emerald : TONE.neutral}>
              סוכן האינדוקס {worker.online ? "מחובר" : `לא מחובר (נראה ${relativeTime(worker.last_seen)})`}
            </Pill>
          </div>
          <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
            {num(lib.catalog_total)} מסמכים בקטלוג · {num(lib.counts.indexed)} באינדקס
            ({num(lib.indexed_chunks)} קטעים) · {num(lib.flagged_tables)} טבלאות מסומנות לבדיקה
          </p>
          <div className="mt-4 flex flex-wrap gap-1.5">
            {DOC_STATUS_ORDER.filter(s => lib.counts[s] > 0).map(s => (
              <Pill key={s} className={DOC_STATUS[s].className}>
                {DOC_STATUS[s].label}: {num(lib.counts[s])}
              </Pill>
            ))}
          </div>
        </button>
      ))}
    </div>
  )
}
