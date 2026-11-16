"use client"

import { useCallback, useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { AdminSettings, AdminUser, getUsers, patchUser, UserStatus } from "@/lib/admin-api"
import { cn } from "@/lib/utils"
import { CARD, ErrorNote, num, Pill, relativeTime, shortDate, usd, USER_STATUS } from "./shared"

const ORDER: Record<UserStatus, number> = { pending: 0, approved: 1, blocked: 2 }

export function UsersTab({ token }: { token: string | undefined }) {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [settings, setSettings] = useState<AdminSettings | null>(null)
  const [filter, setFilter] = useState<UserStatus | null>(null)
  const [q, setQ] = useState("")
  const [error, setError] = useState("")
  const [loaded, setLoaded] = useState(false)

  const load = useCallback(() => {
    getUsers(token)
      .then(data => { setUsers(data.users); setSettings(data.settings); setLoaded(true) })
      .catch(e => setError(e.message))
  }, [token])

  useEffect(() => { load() }, [load])

  const update = async (sub: string, patch: Parameters<typeof patchUser>[1]) => {
    setError("")
    try {
      const updated = await patchUser(sub, patch, token)
      setUsers(prev => prev.map(u => (u.sub === sub ? { ...u, ...updated } : u)))
    } catch (e) {
      setError(e instanceof Error ? e.message : "העדכון נכשל")
    }
  }

  const counts = { pending: 0, approved: 0, blocked: 0 }
  users.forEach(u => { counts[u.status] += 1 })
  const needle = q.trim().toLowerCase()
  const visible = users
    .filter(u => !filter || u.status === filter)
    .filter(u => !needle || u.email.toLowerCase().includes(needle) || (u.name ?? "").toLowerCase().includes(needle))
    .sort((a, b) => ORDER[a.status] - ORDER[b.status])

  if (!loaded) return error ? <ErrorNote message={error} /> : <p className="text-sm text-muted-foreground">טוען…</p>

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <button onClick={() => setFilter(null)} className="chip" data-active={!filter}>
          הכל: {num(users.length)}
        </button>
        {(Object.keys(USER_STATUS) as UserStatus[]).map(s => (
          <button key={s} onClick={() => setFilter(filter === s ? null : s)} className="chip" data-active={filter === s}>
            {USER_STATUS[s].label}: {num(counts[s])}
          </button>
        ))}
        <span className="flex-1" />
        <Input className="w-full sm:w-64" placeholder="חיפוש לפי שם או אימייל" value={q} onChange={e => setQ(e.target.value)} />
      </div>

      {settings && (
        <p className="text-xs text-muted-foreground">
          {settings.require_approval
            ? "משתמש חדש שנכנס עם גוגל ממתין לאישור שלך לפני שיוכל לשאול."
            : "המערכת פתוחה: כל מי שנכנס עם גוגל יכול לשאול (אפשר לשנות בהגדרות)."}
          {" "}מכסת ברירת המחדל: {settings.default_daily_limit > 0 ? `${settings.default_daily_limit} שאלות ביום` : "ללא הגבלה"}.
        </p>
      )}
      <ErrorNote message={error} />

      <div className={cn(CARD, "overflow-x-auto")}>
        <table className="data-table">
          <thead>
            <tr>
              <th>משתמש</th>
              <th>מצב</th>
              <th>שאלות היום</th>
              <th>מכסה יומית</th>
              <th>שאלות החודש</th>
              <th>עלות החודש</th>
              <th>נראה לאחרונה</th>
              <th>פעולות</th>
            </tr>
          </thead>
          <tbody>
            {visible.map(u => (
              <tr key={u.sub}>
                <td>
                  <p className="font-medium">{u.name || "—"}</p>
                  <p dir="ltr" className="mt-0.5 text-right text-xs text-muted-foreground">{u.email}</p>
                  <p className="text-[11px] text-muted-foreground">הצטרף {shortDate(u.created_at)}</p>
                </td>
                <td><Pill className={USER_STATUS[u.status].className}>{USER_STATUS[u.status].label}</Pill></td>
                <td className="tabular-nums">{num(u.questions_today)}</td>
                <td>
                  <LimitEditor
                    value={u.daily_limit}
                    fallback={settings?.default_daily_limit ?? 0}
                    onSave={v => update(u.sub, v === null ? { clear_daily_limit: true } : { daily_limit: v })}
                  />
                </td>
                <td className="tabular-nums">{num(u.questions_month)}</td>
                <td className="tabular-nums">{usd(u.cost_month)}</td>
                <td className="whitespace-nowrap text-xs text-muted-foreground">{relativeTime(u.last_seen_at)}</td>
                <td>
                  <div className="flex gap-1">
                    {u.status !== "approved" && (
                      <Button size="sm" onClick={() => update(u.sub, { status: "approved" })}>אשר</Button>
                    )}
                    {u.status !== "blocked" && (
                      <Button size="sm" variant="outline" onClick={() => update(u.sub, { status: "blocked" })}>חסום</Button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {visible.length === 0 && (
              <tr><td colSpan={8} className="py-12 text-center text-sm text-muted-foreground">אין משתמשים להצגה.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/** Personal daily limit; empty means "use the default". */
function LimitEditor({ value, fallback, onSave }: {
  value: number | null
  fallback: number
  onSave: (value: number | null) => void
}) {
  const [draft, setDraft] = useState(value === null ? "" : String(value))
  useEffect(() => { setDraft(value === null ? "" : String(value)) }, [value])
  const changed = draft !== (value === null ? "" : String(value))
  return (
    <div className="flex items-center gap-1">
      <Input
        type="number"
        min={0}
        className="h-8 w-20"
        value={draft}
        placeholder={fallback > 0 ? String(fallback) : "∞"}
        title="ריק = ברירת המחדל, 0 = ללא הגבלה"
        onChange={e => setDraft(e.target.value)}
      />
      {changed && (
        <Button size="sm" variant="outline" className="h-8"
          onClick={() => onSave(draft === "" ? null : Math.max(0, Number(draft) || 0))}>
          שמור
        </Button>
      )}
    </div>
  )
}
