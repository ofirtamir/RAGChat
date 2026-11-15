"use client"

import { useEffect, useLayoutEffect, useRef, useState } from "react"
import { DocumentsDialog } from "@/components/documents-dialog"
import { UserMenu } from "@/components/user-menu"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { DocumentInfo } from "@/types/chat"
import { BookOpen, PanelRightClose, SquarePen, Trash2 } from "lucide-react"
import { cn } from "@/lib/utils"

// useLayoutEffect logs a warning on the server. Use useEffect on SSR.
const useIsoLayoutEffect = typeof window !== "undefined" ? useLayoutEffect : useEffect

/**
 * Renders a chat-history title that ALWAYS shows the start of the sentence,
 * truncating the end with an ellipsis if it overflows. CSS-based RTL
 * truncation is unreliable across browsers, so we measure and truncate
 * the string in JS. When the text is truncated, a tooltip with the full
 * title is shown on hover.
 */
function HistoryTitle({ title, active }: { title: string; active: boolean }) {
  const containerRef = useRef<HTMLSpanElement>(null)
  const [displayText, setDisplayText] = useState(title)
  const [isTruncated, setIsTruncated] = useState(false)

  useIsoLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return

    const measure = () => {
      const containerWidth = el.clientWidth
      if (containerWidth <= 0) return

      // Build a hidden measurer that copies the typography of the target.
      const measurer = document.createElement("span")
      const style = window.getComputedStyle(el)
      measurer.style.cssText = "visibility:hidden;position:absolute;top:-9999px;left:-9999px;white-space:nowrap;"
      measurer.style.fontFamily = style.fontFamily
      measurer.style.fontSize = style.fontSize
      measurer.style.fontWeight = style.fontWeight
      measurer.style.letterSpacing = style.letterSpacing
      document.body.appendChild(measurer)

      try {
        measurer.textContent = title
        if (measurer.offsetWidth <= containerWidth) {
          setDisplayText(title)
          setIsTruncated(false)
          return
        }

        // Binary search the largest prefix that fits when followed by "…".
        let lo = 0
        let hi = title.length
        while (lo < hi) {
          const mid = Math.ceil((lo + hi) / 2)
          measurer.textContent = title.slice(0, mid) + "…"
          if (measurer.offsetWidth <= containerWidth) {
            lo = mid
          } else {
            hi = mid - 1
          }
        }

        setDisplayText(title.slice(0, lo) + "…")
        setIsTruncated(true)
      } finally {
        document.body.removeChild(measurer)
      }
    }

    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [title])

  const inner = (
    <span
      ref={containerRef}
      dir="rtl"
      className={cn(
        "block flex-1 min-w-0 whitespace-nowrap overflow-hidden text-right",
        active && "text-foreground"
      )}
    >
      {displayText}
    </span>
  )

  if (!isTruncated) return inner

  return (
    <Tooltip delayDuration={200}>
      <TooltipTrigger asChild>{inner}</TooltipTrigger>
      <TooltipContent side="left" align="center" className="max-w-xs break-words" dir="rtl">
        {title}
      </TooltipContent>
    </Tooltip>
  )
}

interface SidebarProps {
  sessions: { id: string; title: string }[]
  activeSessionId: string
  onNewChat: () => void
  onSelectSession: (id: string) => void
  onDeleteSession?: (id: string) => void
  documents: DocumentInfo[]
  totalChunks: number
  onUpload: (files: File[]) => void
  onClearDocuments: () => void
  isUploading: boolean
  uploadError?: string
  /** Desktop: whether the sidebar is expanded. */
  open: boolean
  /** Phone: whether the sidebar drawer is showing. */
  mobileOpen: boolean
  onClose: () => void
}

/** One row in the sidebar's top navigation ("new chat", "documents"). */
function NavRow({ icon: Icon, label, hint, onClick }: {
  icon: React.ComponentType<{ className?: string }>
  label: string
  hint?: React.ReactNode
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className="flex h-9 w-full items-center gap-2.5 rounded-lg px-2.5 text-sm text-foreground transition-colors hover:bg-accent"
    >
      <Icon className="h-[18px] w-[18px] shrink-0" />
      <span className="flex-1 truncate text-right">{label}</span>
      {hint !== undefined && <span className="shrink-0 text-xs text-muted-foreground">{hint}</span>}
    </button>
  )
}

export function Sidebar({
  sessions, activeSessionId, onNewChat, onSelectSession, onDeleteSession,
  documents, totalChunks, onUpload, onClearDocuments, isUploading, uploadError,
  open, mobileOpen, onClose,
}: SidebarProps) {
  const [docsOpen, setDocsOpen] = useState(false)

  // On a phone the sidebar is a drawer: picking something closes it.
  const closeOnPhone = () => {
    if (window.matchMedia("(max-width: 767px)").matches) onClose()
  }

  return (
    <>
      {/* Phone backdrop */}
      <div
        onClick={onClose}
        aria-hidden
        className={cn(
          "fixed inset-0 z-30 bg-black/30 transition-opacity md:hidden",
          mobileOpen ? "opacity-100" : "pointer-events-none opacity-0"
        )}
      />

      <aside
        aria-label="סרגל צד"
        className={cn(
          "z-40 flex h-full shrink-0 flex-col overflow-hidden bg-sidebar transition-[width,transform] duration-200 ease-out",
          // phone: an off-canvas drawer on the right
          "fixed inset-y-0 right-0 w-[280px]",
          mobileOpen ? "translate-x-0 shadow-xl" : "translate-x-full",
          // desktop: part of the layout, collapsible
          "md:static md:translate-x-0 md:shadow-none",
          open ? "md:w-[260px]" : "md:w-0",
        )}
      >
        <div className="flex min-h-0 w-[280px] flex-1 flex-col md:w-[260px]">
          {/* Header */}
          <div className="flex h-14 shrink-0 items-center justify-between px-3">
            <span className="px-1.5 text-[17px] font-semibold tracking-tight">שמאות AI</span>
            <button
              onClick={onClose}
              className="flex h-9 w-9 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              aria-label="סגירת סרגל הצד"
              title="סגירת סרגל הצד"
            >
              <PanelRightClose className="h-5 w-5" />
            </button>
          </div>

          {/* Navigation */}
          <nav className="space-y-0.5 px-2">
            <NavRow icon={SquarePen} label="שיחה חדשה" onClick={() => { onNewChat(); closeOnPhone() }} />
            <NavRow
              icon={BookOpen}
              label={isUploading ? "מעלה מסמכים…" : "מסמכי שמאות"}
              hint={documents.length > 0 ? `${documents.length} קבצים · ${totalChunks} קטעים` : undefined}
              onClick={() => setDocsOpen(true)}
            />
            {uploadError && (
              <p className="px-2.5 pt-1 text-xs leading-snug text-destructive">{uploadError}</p>
            )}
          </nav>

          {/* Chat history */}
          <div className="scrollbar-thin mt-6 min-h-0 flex-1 overflow-y-auto px-2 pb-3">
            {sessions.length > 0 && (
              <>
                <p className="px-2.5 pb-1.5 text-xs font-medium text-muted-foreground">שיחות</p>
                <TooltipProvider delayDuration={200}>
                  {sessions.map(s => {
                    const active = s.id === activeSessionId
                    return (
                      <div
                        key={s.id}
                        dir="rtl"
                        className={cn(
                          "group flex h-9 items-center rounded-lg transition-colors",
                          active ? "bg-accent" : "text-foreground/90 hover:bg-accent/60"
                        )}
                      >
                        <button
                          onClick={() => { onSelectSession(s.id); closeOnPhone() }}
                          dir="rtl"
                          className="flex h-full min-w-0 flex-1 items-center px-2.5 text-sm"
                        >
                          <HistoryTitle title={s.title} active={active} />
                        </button>
                        {onDeleteSession && (
                          <button
                            onClick={e => {
                              e.stopPropagation()
                              onDeleteSession(s.id)
                            }}
                            className={cn(
                              "me-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-opacity",
                              "opacity-0 hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100",
                              active && "max-md:opacity-100"
                            )}
                            aria-label="מחק שיחה"
                            title="מחק שיחה"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        )}
                      </div>
                    )
                  })}
                </TooltipProvider>
              </>
            )}
          </div>

          {/* Signed-in user, pinned to the bottom */}
          <div className="shrink-0 border-t p-2">
            <UserMenu />
          </div>
        </div>
      </aside>

      {/* Documents dialog — rendered outside the aside to avoid stacking-context issues */}
      <DocumentsDialog
        open={docsOpen}
        onOpenChange={setDocsOpen}
        documents={documents}
        totalChunks={totalChunks}
        onUpload={onUpload}
        onClearDocuments={onClearDocuments}
        isUploading={isUploading}
        uploadError={uploadError}
      />
    </>
  )
}
