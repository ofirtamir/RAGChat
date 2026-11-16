"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { signOut, useSession } from "next-auth/react"
import { ArrowRight, BarChart3, FileText, LayoutDashboard, Settings, Users } from "lucide-react"
import { Button } from "@/components/ui/button"
import { getMe, Me } from "@/lib/admin-api"
import { cn } from "@/lib/utils"
import { LibraryTab } from "./library-tab"
import { OverviewTab } from "./overview-tab"
import { SettingsTab } from "./settings-tab"
import { UsageTab } from "./usage-tab"
import { UsersTab } from "./users-tab"

const TABS = [
  { id: "overview", label: "סקירה", icon: LayoutDashboard },
  { id: "library", label: "מסמכים", icon: FileText },
  { id: "users", label: "משתמשים", icon: Users },
  { id: "usage", label: "שימוש ועלויות", icon: BarChart3 },
  { id: "settings", label: "הגדרות", icon: Settings },
] as const

type TabId = (typeof TABS)[number]["id"]

export function AdminConsole() {
  const { data: session, status } = useSession()
  const token = session?.idToken
  const [me, setMe] = useState<Me | null>(null)
  const [error, setError] = useState("")

  useEffect(() => {
    if (status !== "authenticated") return
    getMe(token).then(setMe).catch(e => setError(e.message))
  }, [status, token])

  if (error) {
    // The Google token the backend checks is short-lived; signing in again renews it.
    return (
      <Centered>
        <p className="text-sm text-muted-foreground">לא ניתן לאמת את ההתחברות ({error}).</p>
        <Button onClick={() => signOut({ callbackUrl: "/login?callbackUrl=/admin" })}>התחבר מחדש</Button>
      </Centered>
    )
  }
  if (!me) return <Centered><p className="text-sm text-muted-foreground">טוען…</p></Centered>
  if (!me.is_admin) {
    return (
      <Centered>
        <p className="text-lg font-semibold">הדף לא נמצא</p>
        <Link href="/" className="text-sm text-sky-600 hover:underline">חזרה לצ׳אט</Link>
      </Centered>
    )
  }

  return <AdminShell token={token} />
}

/** The console itself: tab bar + the selected screen. */
export function AdminShell({ token }: { token: string | undefined }) {
  const [tab, setTab] = useState<TabId>("overview")

  return (
    <div dir="rtl" className="min-h-screen bg-muted/20">
      <header className="sticky top-0 z-10 border-b bg-background">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <h1 className="text-lg font-bold">ניהול המערכת</h1>
          <nav className="flex flex-wrap gap-1">
            {TABS.map(t => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={cn(
                  "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm transition-colors",
                  tab === t.id ? "bg-sky-100 font-medium text-sky-800 dark:bg-sky-950 dark:text-sky-200" : "hover:bg-accent",
                )}
              >
                <t.icon className="h-4 w-4" />
                {t.label}
              </button>
            ))}
          </nav>
          <span className="flex-1" />
          <Link href="/" className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
            <ArrowRight className="h-4 w-4" />
            חזרה לצ׳אט
          </Link>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
        {tab === "overview" && <OverviewTab token={token} onNavigate={setTab} />}
        {tab === "library" && <LibraryTab token={token} />}
        {tab === "users" && <UsersTab token={token} />}
        {tab === "usage" && <UsageTab token={token} />}
        {tab === "settings" && <SettingsTab token={token} />}
      </main>
    </div>
  )
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div dir="rtl" className="flex min-h-screen flex-col items-center justify-center gap-4 text-center">
      {children}
    </div>
  )
}
