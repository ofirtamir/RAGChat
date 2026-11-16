"use client"

import { useEffect, useRef, useState } from "react"
import { useSession } from "next-auth/react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { ExternalLink, Loader2 } from "lucide-react"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { getSourceDocument, SourceDocument } from "@/lib/api"
import { CitationDetail } from "@/types/chat"
import { cn } from "@/lib/utils"

/** Opens the document a citation points to, scrolled to the cited page. */
export function SourceViewer({ citation, onClose }: {
  citation: CitationDetail | null
  onClose: () => void
}) {
  const { data: session } = useSession()
  const token = session?.idToken
  const [doc, setDoc] = useState<SourceDocument | null>(null)
  const [error, setError] = useState("")
  const citedPageRef = useRef<HTMLElement | null>(null)

  const agentId = citation?.agent_id
  const key = citation?.record_key
  const citedPage = citation?.page ?? null

  useEffect(() => {
    setDoc(null)
    setError("")
    if (!agentId || !key) return
    let cancelled = false
    getSourceDocument(agentId, key, token)
      .then(d => !cancelled && setDoc(d))
      .catch(e => !cancelled && setError(e instanceof Error ? e.message : "טעינת המסמך נכשלה"))
    return () => { cancelled = true }
  }, [agentId, key, token])

  useEffect(() => {
    if (doc) citedPageRef.current?.scrollIntoView({ block: "start" })
  }, [doc])

  return (
    <Dialog open={!!citation} onOpenChange={open => !open && onClose()}>
      <DialogContent dir="rtl" className="flex h-[90vh] max-w-4xl flex-col gap-4 text-right">
        <DialogHeader className="space-y-2 pe-8 text-right sm:text-right">
          <DialogTitle className="text-lg font-semibold leading-snug">
            {doc?.title ?? citation?.source ?? "טוען…"}
          </DialogTitle>
          <DialogDescription asChild>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              {doc?.pages != null && <span>{doc.pages} עמודים</span>}
              {citedPage != null && <span>· הציטוט מעמוד {citedPage + 1}</span>}
            </div>
          </DialogDescription>
        </DialogHeader>

        {doc && doc.files.length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            {doc.files.map((f, i) => (
              <a
                key={f.url}
                href={f.url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-[13px] transition-colors hover:bg-accent"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                פתח את המסמך המקורי{doc.files.length > 1 ? ` ${i + 1}` : ""}
              </a>
            ))}
          </div>
        )}

        <div className="scrollbar-thin min-h-0 flex-1 space-y-3 overflow-y-auto rounded-2xl bg-muted/50 p-3 sm:p-4">
          {error && <p className="py-10 text-center text-sm text-destructive">{error}</p>}
          {!doc && !error && (
            <div className="flex justify-center py-10 text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
          )}
          {doc && doc.parsed_pages.length === 0 && (
            <p className="py-10 text-center text-sm text-muted-foreground">
              התוכן של המסמך אינו זמין לתצוגה. אפשר לפתוח את הקובץ המקורי.
            </p>
          )}
          {doc?.parsed_pages.map(page => {
            const cited = page.number === citedPage
            return (
              <section
                key={page.number}
                ref={cited ? citedPageRef : undefined}
                className={cn(
                  "scroll-mt-3 rounded-xl border bg-background p-4 sm:p-5",
                  cited && "border-blue-400 ring-2 ring-blue-400/30 dark:border-blue-400/60"
                )}
              >
                <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
                  <span className="font-medium">עמוד {page.number + 1}</span>
                  {cited && (
                    <span className="rounded-full bg-blue-50 px-2 py-0.5 text-blue-700 dark:bg-blue-400/10 dark:text-blue-300">
                      העמוד המצוטט
                    </span>
                  )}
                </div>
                <div className="prose prose-sm max-w-none overflow-x-auto dark:prose-invert prose-table:text-xs">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{page.markdown}</ReactMarkdown>
                </div>
              </section>
            )
          })}
        </div>
      </DialogContent>
    </Dialog>
  )
}
