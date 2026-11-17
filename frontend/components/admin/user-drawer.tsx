"use client"

import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { patchUser, UserStatus } from "@/lib/admin-api"
import { getUserActivity, UserActivity } from "@/lib/admin-insights-api"
import { QuestionLog } from "./quality-tab"
import { DailyBars, dateTime, ErrorNote, num, Pill, shortDate, StatCard, usdPrecise, USER_STATUS } from "./shared"

/** One user up close: activity, satisfaction, cost, recent questions, status and a private note. */
export function UserDrawer({ sub, token, onClose, onChanged }: {
  sub: string | null
  token: string | undefined
  onClose: () => void
  onChanged?: () => void
}) {
  const [data, setData] = useState<UserActivity | null>(null)
  const [error, setError] = useState("")
  const [note, setNote] = useState("")

  const load = (s: string) =>
    getUserActivity(s, token).then(d => { setData(d); setNote(d.user.note ?? "") }).catch(e => setError(e.message))

  useEffect(() => {
    setData(null)
    setError("")
    if (sub) load(sub)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sub, token])

  const update = async (patch: { status?: UserStatus; note?: string }) => {
    if (!sub) return
    try {
      await patchUser(sub, patch, token)
      await load(sub)
      onChanged?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : "העדכון נכשל")
    }
  }

  const u = data?.user
  return (
    <Dialog open={!!sub} onOpenChange={open => !open && onClose()}>
      <DialogContent dir="rtl" className="flex h-[92vh] max-w-4xl flex-col gap-4 text-right">
        <DialogHeader className="space-y-1.5 pe-8 text-right sm:text-right">
          <DialogTitle className="text-lg font-semibold">{u ? (u.name || u.email) : "טוען…"}</DialogTitle>
          <DialogDescription asChild>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              {u && (
                <>
                  <span dir="ltr">{u.email}</span>
                  <Pill className={USER_STATUS[u.status].className}>{USER_STATUS[u.status].label}</Pill>
                  <span>· הצטרף {shortDate(u.created_at)}</span>
                  <span>· נראה לאחרונה {dateTime(u.last_seen_at)}</span>
                </>
              )}
            </div>
          </DialogDescription>
        </DialogHeader>
        <ErrorNote message={error} />
        {data && u && (
          <div className="scrollbar-thin min-h-0 flex-1 space-y-4 overflow-y-auto">
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <StatCard label="שאלות (סה״כ)" value={num(data.questions)}
                hint={`${num(data.active_days)} ימים פעילים · ראשונה ${shortDate(data.first_ts)}`} />
              <StatCard label="דירוגים" value={`👍 ${num(data.up)} · 👎 ${num(data.down)}`}
                tone={data.down > data.up ? "warn" : undefined}
                hint={`${num(data.no_answer)} פעמים לא נמצאה תשובה`} />
              <StatCard label="עלות (סה״כ)" value={usdPrecise(data.cost)}
                hint={`החודש ${usdPrecise(u.cost_month)} · ${num(u.questions_month)} שאלות`} />
              <StatCard label="נחסם (30 יום)" value={num(data.refused)} tone={data.refused ? "warn" : undefined}
                hint={data.refused ? "הגיע למכסה — אולי כדאי להעלות לו מכסה" : `שגיאות: ${num(data.errors)}`} />
            </div>
            <div className="rounded-2xl border p-4">
              <p className="mb-3 text-[13px] text-muted-foreground">שאלות ביום (30 יום)</p>
              <DailyBars days={data.daily} pick={d => d.questions} format={n => `${n} שאלות`} height="h-20" />
            </div>
            <div className="flex flex-wrap items-end gap-2">
              <label className="grow text-xs text-muted-foreground">
                הערה פרטית (רק אתה רואה)
                <textarea className="mt-1 block h-16 w-full rounded-lg border bg-background p-2 text-sm"
                  value={note} onChange={e => setNote(e.target.value)} maxLength={500} />
              </label>
              <Button size="sm" variant="outline" disabled={note === (u.note ?? "")} onClick={() => update({ note })}>שמור הערה</Button>
              {u.status !== "approved" && <Button size="sm" onClick={() => update({ status: "approved" })}>אשר</Button>}
              {u.status !== "blocked" && <Button size="sm" variant="outline" onClick={() => update({ status: "blocked" })}>חסום</Button>}
            </div>
            <QuestionLog token={token} days={366} user={u.sub} compact />
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
