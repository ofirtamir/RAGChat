"use client"

import { useCallback, useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { AdminSettings, AdminUser, getUsers, patchUser, UserStatus } from "@/lib/admin-api"
import { cn } from "@/lib/utils"
import { ErrorNote, num, Pill, relativeTime, shortDate, usd, USER_STATUS } from "./shared"

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
        <button onClick={() => setFilter(null)}
          className={cn("rounded-full border px-3 py-1 text-xs", !filter && "border-sky-500 bg-sky-50 dark:bg-sky-950")}>
          הכל: {num(users.length)}
        </button>
        {(Object.keys(USER_STATUS) as UserStatus[]).map(s => (
          <button key={s} onClick={() => setFilter(filter === s ? null : s)}
            className={cn("rounded-full border px-3 py-1 text-xs", filter === s && "border-sky-500 bg-sky-50 dark:bg-sky-950")}>
            {USER_STATUS[s].label}: {num(counts[s])}
          </button>
        ))}
        <span className="flex-1" />
        <Input className="h-9 w-56" placeholder="חיפוש לפי שם או אימייל" value={q} onChange={e => setQ(e.target.value)} />
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

      <div className="overflow-x-auto rounded-xl border bg-card">
        <table className="w-full text-sm">
          <thead className="border-b bg-muted/40 text-xs text-muted-foreground">
            <tr>
              <th className="p-2 text-right font-medium">משתמש</th>
              <th className="p-2 text-right font-medium">מצב</th>
              <th className="p-2 text-right font-medium">שאלות היום</th>
              <th className="p-2 text-right font-medium">מכסה יומית</th>
              <th className="p-2 text-right font-medium">שאלות החודש</th>
              <th className="p-2 text-right font-medium">עלות החודש</th>
              <th className="p-2 text-right font-medium">נראה לאחרונה</th>
              <th className="p-2 text-right font-medium">פעולות</th>
            </tr>
          </thead>
          <tbody>
            {visible.map(u => (
              <tr key={u.sub} className="border-b last:border-0">
                <td className="p-2">
                  <p className="font-medium">{u.name || "—"}</p>
                  <p dir="ltr" className="text-right text-xs text-muted-foreground">{u.email}</p>
                  <p className="text-[11px] text-muted-foreground">הצטרף {shortDate(u.created_at)}</p>
                </td>
                <td className="p-2"><Pill className={USER_STATUS[u.status].className}>{USER_STATUS[u.status].label}</Pill></td>
                <td className="p-2 tabular-nums">{num(u.questions_today)}</td>
                <td className="p-2">
                  <LimitEditor
                    value={u.daily_limit}
                    fallback={settings?.default_daily_limit ?? 0}
                    onSave={v => update(u.sub, v === null ? { clear_daily_limit: true } : { daily_limit: v })}
                  />
                </td>
                <td className="p-2 tabular-nums">{num(u.questions_month)}</td>
                <td className="p-2 tabular-nums">{usd(u.cost_month)}</td>
                <td className="whitespace-nowrap p-2 text-xs text-muted-foreground">{relativeTime(u.last_seen_at)}</td>
                <td className="p-2">
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
              <tr><td colSpan={8} className="p-8 text-center text-sm text-muted-foreground">אין משתמשים להצגה.</td></tr>
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
