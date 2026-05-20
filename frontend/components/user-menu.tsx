"use client"

import { useSession, signOut } from "next-auth/react"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { LogOut, User as UserIcon } from "lucide-react"

export function UserMenu() {
  const { data: session, status } = useSession()

  if (status === "loading") {
    return <div className="h-9 w-9 rounded-full bg-muted animate-pulse" />
  }

  if (!session?.user) return null

  const initials = session.user.name
    ? session.user.name.split(" ").map(s => s[0]).slice(0, 2).join("")
    : "??"

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          className="flex items-center gap-2 w-full p-2 rounded-lg hover:bg-accent transition-colors text-right"
          aria-label="תפריט משתמש"
        >
          <Avatar className="h-9 w-9 shrink-0">
            {session.user.image && <AvatarImage src={session.user.image} alt={session.user.name ?? ""} />}
            <AvatarFallback className="text-xs bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-300">
              {initials}
            </AvatarFallback>
          </Avatar>
          <div className="min-w-0 flex-1 text-right">
            <p className="text-xs font-medium truncate text-foreground">{session.user.name}</p>
            <p className="text-[10px] text-muted-foreground truncate">{session.user.email}</p>
          </div>
        </button>
      </DropdownMenuTrigger>

      <DropdownMenuContent align="end" className="w-56 text-right">
        <DropdownMenuLabel className="flex items-center gap-2">
          <UserIcon className="w-3.5 h-3.5" />
          <span className="text-xs truncate">{session.user.email}</span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onClick={() => signOut({ callbackUrl: "/login" })}
          className="text-destructive focus:text-destructive cursor-pointer"
        >
          <LogOut className="w-4 h-4 ms-2" />
          התנתק
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
