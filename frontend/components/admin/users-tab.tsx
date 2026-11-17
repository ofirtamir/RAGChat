"use client"

import { useCallback, useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Download } from "lucide-react"
import { AdminSettings, AdminUser, getUsers, patchUser, UserStatus } from "@/lib/admin-api"
import { bulkUserStatus, downloadExport } from "@/lib/admin-insights-api"
import { cn } from "@/lib/utils"
import { CARD, ErrorNote, num, Pill, relativeTime, shortDate, usd, USER_STATUS } from "./shared"
import { UserDrawer } from "./user-drawer"

const ORDER: Record<UserStatus, number> = { pending: 0, approved: 1, blocked: 2 }
const DAY_MS = 24 * 60 * 60 * 1000

type SortKey = "status" | "last_seen" | "questions_month" | "cost_month" | "created"
const SORTS: Record<SortKey, { label: string; compare: (a: AdminUser, b: AdminUser) => number }> = {
  status:          { label: "ממתינים קודם", compare: (a, b) => ORDER[a.status] - ORDER[b.status] },
  last_seen:       { label: "נראו לאחרונה", compare: (a, b) => b.last_seen_at.localeCompare(a.last_seen_at) },
  questions_month: { label: "הכי הרבה שאלות החודש", compare: (a, b) => b.questions_month - a.questions_month },
  cost_month:      { label: "היקרים ביותר החודש", compare: (a, b) => b.cost_month - a.cost_month },
  created:         { label: "הצטרפו לאחרונה", compare: (a, b) => b.created_at.localeCompare(a.created_at) },
}

export function UsersTab({ token }: { token: string | undefined }) {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [settings, setSettings] = useState<AdminSettings | null>(null)
  const [filter, setFilter] = useState<UserStatus | null>(null)
  const [q, setQ] = useState("")
  const [error, setError] = useState("")
  const [loaded, setLoaded] = useState(false)
  const [sort, setSort] = useState<SortKey>("status")
  const [inactive, setInactive] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [openUser, setOpenUser] = useState<string | null>(null)
  const [notice, setNotice] = useState("")

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

  const bulk = async (status: UserStatus) => {
    const subs = Array.from(selected)
    if (status === "blocked" && !window.confirm(`לחסום ${subs.length} משתמשים?`)) return
    setError("")
    try {
      const res = await bulkUserStatus(subs, status, token)
      setNotice(`${num(res.updated)} משתמשים עודכנו`)
      setSelected(new Set())
      load()
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
    .filter(u => !inactive || Date.now() - new Date(u.last_seen_at).getTime() > 14 * DAY_MS)
    .sort(SORTS[sort].compare)
  const allSelected = visible.length > 0 && visible.every(u => selected.has(u.sub))
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(visible.map(u => u.sub)))
  const toggle = (sub: string) => setSelected(prev => {
    const next = new Set(prev)
    if (next.has(sub)) next.delete(sub)
    else next.add(sub)
    return next
  })

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
        <button onClick={() => setInactive(!inactive)} className="chip" data-active={inactive}
          title="לא נכנסו למערכת 14 יום ומעלה">
          לא נכנסו 14+ יום
        </button>
        <span className="flex-1" />
        <select className="h-9 rounded-lg border bg-background px-2 text-sm" value={sort}
          onChange={e => setSort(e.target.value as SortKey)}>
          {(Object.keys(SORTS) as SortKey[]).map(k => <option key={k} value={k}>{SORTS[k].label}</option>)}
        </select>
        <Input className="w-full sm:w-64" placeholder="חיפוש לפי שם או אימייל" value={q} onChange={e => setQ(e.target.value)} />
        <Button size="sm" variant="outline" className="h-9 gap-1"
          onClick={() => downloadExport("users", {}, token).catch(e => setError(e.message))}>
          <Download className="h-3.5 w-3.5" /> CSV
        </Button>
      </div>

      {selected.size > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border bg-muted/50 px-4 py-2 text-sm">
          <span>{num(selected.size)} נבחרו</span>
          <Button size="sm" onClick={() => bulk("approved")}>אשר את כולם</Button>
          <Button size="sm" variant="outline" onClick={() => bulk("blocked")}>חסום</Button>
          <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>בטל בחירה</Button>
        </div>
      )}
      {notice && <p className="text-xs text-emerald-700 dark:text-emerald-300">{notice}</p>}

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
              <th className="w-8"><input type="checkbox" checked={allSelected} onChange={toggleAll} aria-label="בחר הכל" /></th>
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
                <td><input type="checkbox" checked={selected.has(u.sub)} onChange={() => toggle(u.sub)} aria-label="בחר" /></td>
                <td className="cursor-pointer" onClick={() => setOpenUser(u.sub)}>
                  <p className="font-medium underline-offset-2 hover:underline">{u.name || "—"}</p>
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
              <tr><td colSpan={9} className="py-12 text-center text-sm text-muted-foreground">אין משתמשים להצגה.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <UserDrawer sub={openUser} token={token} onClose={() => setOpenUser(null)} onChanged={load} />
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
