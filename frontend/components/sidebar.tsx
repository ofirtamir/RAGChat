"use client"

import { useEffect, useLayoutEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { ThemeToggle } from "@/components/theme-toggle"
import { DocumentsDialog } from "@/components/documents-dialog"
import { UserMenu } from "@/components/user-menu"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { DocumentInfo } from "@/types/chat"
import { Plus, MessageSquare, Database, BookOpen, Trash2 } from "lucide-react"
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
        active && "font-medium"
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
}

export function Sidebar({
  sessions, activeSessionId, onNewChat, onSelectSession, onDeleteSession,
  documents, totalChunks, onUpload, onClearDocuments, isUploading, uploadError,
}: SidebarProps) {
  const [docsOpen, setDocsOpen] = useState(false)

  return (
    <>
      <aside className="w-[280px] flex-shrink-0 border-e bg-card flex flex-col h-full">
        {/* Header */}
        <div className="p-4 border-b flex items-center justify-between">
          <div>
            <h1 className="font-bold text-lg bg-gradient-to-l from-sky-500 to-cyan-400 bg-clip-text text-transparent">
              שמאות AI
            </h1>
            <p className="text-xs text-muted-foreground">עוזר שמאות חכם</p>
          </div>
          <ThemeToggle />
        </div>

        {/* New chat button */}
        <div className="p-3">
          <Button onClick={onNewChat} className="w-full gap-2" size="sm">
            <Plus className="w-4 h-4" />
            שיחה חדשה
          </Button>
        </div>

        <Separator />

        {/* Chat history */}
        <div className="flex-1 overflow-y-auto p-2">
          {sessions.length > 0 && (
            <div className="mb-3">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground px-2 py-1">
                היסטוריה
              </p>
              <TooltipProvider delayDuration={200}>
                {sessions.map(s => (
                  <div
                    key={s.id}
                    dir="rtl"
                    className={cn(
                      "group flex items-center gap-1 rounded-lg px-1 transition-colors",
                      s.id === activeSessionId
                        ? "bg-accent text-accent-foreground"
                        : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                    )}
                  >
                    <button
                      onClick={() => onSelectSession(s.id)}
                      dir="rtl"
                      className="flex-1 min-w-0 px-2 py-2 text-sm flex items-center gap-2"
                    >
                      <MessageSquare className="w-3.5 h-3.5 shrink-0" />
                      <HistoryTitle title={s.title} active={s.id === activeSessionId} />
                    </button>
                    {onDeleteSession && (
                      <button
                        onClick={e => {
                          e.stopPropagation()
                          onDeleteSession(s.id)
                        }}
                        className="shrink-0 p-1 rounded opacity-0 group-hover:opacity-100 hover:bg-destructive/15 hover:text-destructive transition-all"
                        aria-label="מחק שיחה"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                ))}
              </TooltipProvider>
            </div>
          )}
        </div>

        <Separator />

        {/* Knowledge base summary */}
        <div className="p-3 space-y-2">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground px-1">
            מסמכי שמאות
          </p>

          {/* Stats row */}
          <div className="flex items-center gap-2 px-1">
            <Database className="w-3.5 h-3.5 text-muted-foreground" />
            <span className="text-xs text-muted-foreground">
              {documents.length > 0 ? (
                <>
                  <span className="font-semibold text-foreground">{documents.length}</span>
                  {" קבצים · "}
                  <span className="font-semibold text-foreground">{totalChunks}</span>
                  {" קטעים"}
                </>
              ) : (
                "לא הועלו קבצים עדיין"
              )}
            </span>
          </div>

          {/* Open dialog button */}
          <Button
            variant="outline"
            size="sm"
            className="w-full gap-2 text-xs"
            onClick={() => setDocsOpen(true)}
          >
            <BookOpen className="w-3.5 h-3.5" />
            {isUploading ? "...מעלה" : "ניהול מסמכים"}
          </Button>

          {uploadError && (
            <p className="text-xs text-destructive px-1 leading-snug">{uploadError}</p>
          )}
        </div>

        <Separator />

        {/* User menu — sits at the bottom of the sidebar */}
        <div className="p-2">
          <UserMenu />
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
