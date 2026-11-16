"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { signOut, useSession } from "next-auth/react"
import { ArrowRight, BarChart3, FileText, LayoutDashboard, Settings, Users } from "lucide-react"
import { Button } from "@/components/ui/button"
import { ThemeToggle } from "@/components/theme-toggle"
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
        <Link href="/" className="text-sm underline underline-offset-4 hover:text-muted-foreground">חזרה לצ׳אט</Link>
      </Centered>
    )
  }

  return <AdminShell token={token} />
}

/** The console itself: tab bar + the selected screen. */
export function AdminShell({ token }: { token: string | undefined }) {
  const [tab, setTab] = useState<TabId>("overview")

  return (
    <div dir="rtl" className="min-h-dvh bg-sidebar">
      <header className="sticky top-0 z-10 border-b bg-sidebar/85 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 sm:px-6">
          <h1 className="text-[17px] font-semibold tracking-tight">ניהול המערכת</h1>
          {/* Tabs: own scrollable row on phones, inline on wider screens */}
          <nav className="order-last -mx-4 flex [scrollbar-width:none] [&::-webkit-scrollbar]:hidden w-[calc(100%+2rem)] sm:-mx-6 sm:w-[calc(100%+3rem)] sm:px-6 gap-1 overflow-x-auto px-4 md:order-none md:mx-0 md:w-auto md:px-0">
            {TABS.map(t => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={cn(
                  "flex shrink-0 items-center gap-1.5 rounded-full px-3.5 py-1.5 text-sm transition-colors",
                  tab === t.id
                    ? "bg-foreground text-background"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground",
                )}
              >
                <t.icon className="h-4 w-4" />
                {t.label}
              </button>
            ))}
          </nav>
          <span className="flex-1" />
          <div className="flex items-center gap-1.5">
            <ThemeToggle />
            <Link
              href="/"
              className="flex h-8 items-center gap-1 rounded-full border bg-background px-3 text-[13px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <ArrowRight className="h-4 w-4" />
              חזרה לצ׳אט
            </Link>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
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
    <div dir="rtl" className="flex min-h-dvh flex-col items-center justify-center gap-4 bg-sidebar px-6 text-center">
      {children}
    </div>
  )
}
