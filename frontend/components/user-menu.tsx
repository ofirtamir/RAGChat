"use client"

import Link from "next/link"
import { useSession, signOut } from "next-auth/react"
import { useTheme } from "next-themes"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { LogOut, Moon, ShieldCheck, Sun } from "lucide-react"
import { useMe } from "@/lib/use-me"

export function UserMenu() {
  const { data: session, status } = useSession()
  const { resolvedTheme, setTheme } = useTheme()
  const me = useMe()

  if (status === "loading") {
    return (
      <div className="flex items-center gap-2.5 p-2">
        <div className="h-8 w-8 rounded-full bg-accent animate-pulse" />
        <div className="h-3 w-24 rounded bg-accent animate-pulse" />
      </div>
    )
  }

  if (!session?.user) return null

  const initials = session.user.name
    ? session.user.name.split(" ").map(s => s[0]).slice(0, 2).join("")
    : "??"
  const dark = resolvedTheme === "dark"

  return (
    <DropdownMenu dir="rtl">
      <DropdownMenuTrigger asChild>
        <button
          className="flex w-full items-center gap-2.5 rounded-lg p-2 text-right transition-colors hover:bg-accent data-[state=open]:bg-accent"
          aria-label="תפריט משתמש"
        >
          <Avatar className="h-8 w-8 shrink-0">
            {session.user.image && <AvatarImage src={session.user.image} alt={session.user.name ?? ""} />}
            <AvatarFallback className="bg-foreground/10 text-xs text-foreground">
              {initials}
            </AvatarFallback>
          </Avatar>
          <div className="min-w-0 flex-1 text-right">
            <p className="truncate text-sm leading-tight text-foreground">{session.user.name}</p>
            {me?.is_admin && <p className="truncate text-xs leading-tight text-muted-foreground">מנהל מערכת</p>}
          </div>
        </button>
      </DropdownMenuTrigger>

      <DropdownMenuContent side="top" align="start" className="w-[var(--radix-dropdown-menu-trigger-width)] min-w-56 text-right">
        <DropdownMenuLabel dir="ltr" className="truncate text-right">{session.user.email}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {me?.is_admin && (
          <DropdownMenuItem asChild className="cursor-pointer">
            <Link href="/admin">
              <ShieldCheck />
              ניהול המערכת
            </Link>
          </DropdownMenuItem>
        )}
        <DropdownMenuItem className="cursor-pointer" onSelect={() => setTheme(dark ? "light" : "dark")}>
          {dark ? <Sun /> : <Moon />}
          {dark ? "מצב בהיר" : "מצב כהה"}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onClick={() => signOut({ callbackUrl: "/login" })}
          className="cursor-pointer"
        >
          <LogOut />
          התנתק
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
