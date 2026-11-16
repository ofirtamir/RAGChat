"use client"

import { RefObject, useCallback, useEffect, useLayoutEffect, useState } from "react"
import { ArrowUp, Loader2, PanelRightOpen, SquarePen } from "lucide-react"
import { cn } from "@/lib/utils"

const SIDEBAR_KEY = "ragchat_sidebar_collapsed"
const isPhone = () => window.matchMedia("(max-width: 767px)").matches

// useLayoutEffect logs a warning on the server. Use useEffect on SSR.
const useIsoLayoutEffect = typeof window !== "undefined" ? useLayoutEffect : useEffect

/**
 * Sidebar visibility. On desktop it is expanded or collapsed (remembered per
 * browser); on a phone it is a drawer that starts closed.
 */
export function useSidebarState() {
  const [open, setOpen] = useState(true)
  const [mobileOpen, setMobileOpen] = useState(false)

  useEffect(() => {
    try { if (localStorage.getItem(SIDEBAR_KEY) === "1") setOpen(false) } catch {}
  }, [])

  const setDesktop = useCallback((next: boolean) => {
    setOpen(next)
    try { localStorage.setItem(SIDEBAR_KEY, next ? "0" : "1") } catch {}
  }, [])

  const toggle = useCallback(() => {
    if (isPhone()) setMobileOpen(o => !o)
    else setDesktop(!open)
  }, [open, setDesktop])

  const close = useCallback(() => {
    if (isPhone()) setMobileOpen(false)
    else setDesktop(false)
  }, [setDesktop])

  return { open, mobileOpen, toggle, close }
}

/** Grow a textarea with its content, up to its CSS max-height. */
export function useAutoGrow(ref: RefObject<HTMLTextAreaElement>, value: string) {
  useIsoLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = "auto"
    el.style.height = `${el.scrollHeight}px`
  }, [ref, value])
}

const SUGGESTIONS = [
  "מה שווי הנכס לפי חוות הדעת?",
  "אילו פגמים נמצאו בנכס?",
  "מהי שיטת ההשוואה ששימשה?",
  "מה עלות הבנייה למ\"ר?",
  "השווה בין שתי שומות",
]

const iconButton =
  "flex h-9 w-9 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"

/** Top bar of the chat. Shows the sidebar toggle and "new chat" while the sidebar is hidden. */
export function ChatTopBar({ sidebarOpen, onToggleSidebar, onNewChat }: {
  sidebarOpen: boolean
  onToggleSidebar: () => void
  onNewChat: () => void
}) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-1 px-2 sm:px-3">
      <div className={cn("flex items-center gap-1", sidebarOpen && "md:hidden")}>
        <button onClick={onToggleSidebar} className={iconButton} aria-label="פתיחת סרגל הצד" title="פתיחת סרגל הצד">
          <PanelRightOpen className="h-5 w-5" />
        </button>
        <button onClick={onNewChat} className={iconButton} aria-label="שיחה חדשה" title="שיחה חדשה">
          <SquarePen className="h-5 w-5" />
        </button>
      </div>
      <span className={cn("px-2 text-[17px] font-medium", sidebarOpen && "md:hidden")}>שמאות AI</span>
    </header>
  )
}

/** Empty conversation: the greeting above the (centred) composer. */
export function Greeting({ firstName }: { firstName?: string }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-end px-4 pb-8">
      <h1 dir="rtl" className="text-center text-[26px] font-normal tracking-tight sm:text-[28px]">
        {firstName ? `טוב לראות אותך, ${firstName}.` : "במה אפשר לעזור היום?"}
      </h1>
    </div>
  )
}

/** Example questions under the composer in an empty conversation. */
export function Suggestions({ onPick }: { onPick: (question: string) => void }) {
  return (
    <div className="mt-5 flex flex-wrap justify-center gap-2">
      {SUGGESTIONS.map(q => (
        <button
          key={q}
          dir="rtl"
          onClick={() => onPick(q)}
          className="rounded-full border px-3.5 py-2 text-[13px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          {q}
        </button>
      ))}
    </div>
  )
}

/** Round send button at the end of the composer pill. */
export function SendButton({ onClick, disabled, loading }: {
  onClick: () => void
  disabled: boolean
  loading: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-foreground text-background transition-opacity hover:opacity-80 disabled:opacity-25"
      aria-label="שליחה"
    >
      {loading
        ? <Loader2 className="h-[18px] w-[18px] animate-spin" />
        : <ArrowUp className="h-5 w-5" strokeWidth={2.25} />}
    </button>
  )
}
