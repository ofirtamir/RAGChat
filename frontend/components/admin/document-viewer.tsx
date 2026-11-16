"use client"

import { useEffect, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { ExternalLink } from "lucide-react"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { getLibraryDoc, LibraryActionName, LibraryDocDetail } from "@/lib/admin-api"
import { cn } from "@/lib/utils"
import { DOC_STATUS, ErrorNote, num, Pill, shortDate, usdPrecise } from "./shared"

/** Shows what the parser produced for one document, page by page, next to its details. */
export function DocumentViewer({ agentId, recordKey, token, onClose, onAction }: {
  agentId: string
  recordKey: string | null
  token: string | undefined
  onClose: () => void
  onAction: (action: LibraryActionName, key: string) => Promise<void>
}) {
  const [doc, setDoc] = useState<LibraryDocDetail | null>(null)
  const [error, setError] = useState("")
  const [raw, setRaw] = useState(false)
  const [onlyFlagged, setOnlyFlagged] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setDoc(null)
    setError("")
    setOnlyFlagged(false)
    if (!recordKey) return
    getLibraryDoc(agentId, recordKey, token).then(setDoc).catch(e => setError(e.message))
  }, [agentId, recordKey, token])

  const act = async (action: LibraryActionName) => {
    if (!doc) return
    setBusy(true)
    try {
      await onAction(action, doc.record_key)
      setDoc(await getLibraryDoc(agentId, doc.record_key, token))
    } catch (e) {
      setError(e instanceof Error ? e.message : "הפעולה נכשלה")
    } finally {
      setBusy(false)
    }
  }

  const flaggedPages = new Map<number, string[]>()
  for (const p of doc?.problems ?? []) {
    flaggedPages.set(p.page, [...(flaggedPages.get(p.page) ?? []), ...p.problems])
  }
  const pages = (doc?.parsed_pages ?? []).filter(p => !onlyFlagged || flaggedPages.has(p.number))

  return (
    <Dialog open={!!recordKey} onOpenChange={open => !open && onClose()}>
      <DialogContent dir="rtl" className="flex h-[92vh] max-w-5xl flex-col gap-3 text-right">
        <DialogHeader className="space-y-2 pe-6 text-right sm:text-right">
          <DialogTitle className="text-base leading-snug">{doc?.title ?? "טוען…"}</DialogTitle>
          <DialogDescription asChild>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              {doc && (
                <>
                  <Pill className={DOC_STATUS[doc.status].className}>{DOC_STATUS[doc.status].label}</Pill>
                  {doc.pages != null && <span>{num(doc.pages)} עמודים</span>}
                  {doc.tables != null && <span>· {num(doc.tables)} טבלאות</span>}
                  {!!doc.flagged_tables && (
                    <span className="text-amber-600">· {num(doc.flagged_tables)} מסומנות לבדיקה</span>
                  )}
                  {doc.chars != null && <span>· {num(doc.chars)} תווים</span>}
                  {doc.chunks != null && <span>· {num(doc.chunks)} קטעים באינדקס</span>}
                  {doc.index_cost_usd != null && (
                    <span>· עלות אינדוקס {usdPrecise(doc.index_cost_usd)} ({num(doc.embed_tokens)} טוקנים)</span>
                  )}
                  {doc.parsed_at && <span>· פורסר {shortDate(doc.parsed_at)}</span>}
                </>
              )}
            </div>
          </DialogDescription>
        </DialogHeader>

        <ErrorNote message={error || doc?.error || ""} />

        {doc && (
          <div className="flex flex-wrap items-center gap-2">
            {doc.files.map((f, i) => (
              <a
                key={f.url}
                href={f.url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 rounded-md border px-2.5 py-1.5 text-xs hover:bg-accent"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                קובץ המקור{doc.files.length > 1 ? ` ${i + 1}` : ""}
              </a>
            ))}
            {(doc.status === "parsed" || doc.status === "indexed") && (
              <Button size="sm" disabled={busy} onClick={() => act("index")}>
                {doc.status === "indexed" ? "אנדקס מחדש" : "הכנס לאינדקס"}
              </Button>
            )}
            {doc.status === "indexed" && (
              <Button size="sm" variant="outline" disabled={busy} onClick={() => act("unindex")}>
                הסר מהאינדקס
              </Button>
            )}
            {["catalog", "parsed", "failed", "no_text"].includes(doc.status) && (
              <Button size="sm" variant="outline" disabled={busy} onClick={() => act("queue")}>
                {doc.status === "catalog" ? "הורד ופרסר" : "פרסר מחדש"}
              </Button>
            )}
            <span className="flex-1" />
            {flaggedPages.size > 0 && (
              <label className="flex items-center gap-1.5 text-xs">
                <input type="checkbox" checked={onlyFlagged} onChange={e => setOnlyFlagged(e.target.checked)} />
                רק עמודים עם טבלה מסומנת ({flaggedPages.size})
              </label>
            )}
            <label className="flex items-center gap-1.5 text-xs">
              <input type="checkbox" checked={raw} onChange={e => setRaw(e.target.checked)} />
              טקסט גולמי
            </label>
          </div>
        )}

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto rounded-lg border bg-muted/30 p-3">
          {doc && pages.length === 0 && (
            <p className="py-10 text-center text-sm text-muted-foreground">
              {doc.parsed_pages.length === 0
                ? "המסמך עדיין לא פורסר. בחר ״הורד ופרסר״ והפעל את סוכן האינדוקס במחשב."
                : "אין עמודים להצגה."}
            </p>
          )}
          {pages.map(page => {
            const problems = flaggedPages.get(page.number)
            return (
              <section
                key={page.number}
                className={cn("rounded-lg border bg-background p-4", problems && "border-amber-400")}
              >
                <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <span className="font-medium">עמוד {page.number + 1}</span>
                  {problems && (
                    <Pill className="bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300">
                      טבלה לבדיקה: <span dir="ltr" className="ms-1">{problems.join(", ")}</span>
                    </Pill>
                  )}
                </div>
                {raw ? (
                  <pre dir="rtl" className="whitespace-pre-wrap break-words text-xs leading-relaxed">
                    {page.markdown}
                  </pre>
                ) : (
                  <div className="prose prose-sm max-w-none overflow-x-auto dark:prose-invert prose-table:text-xs">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{page.markdown}</ReactMarkdown>
                  </div>
                )}
              </section>
            )
          })}
        </div>
      </DialogContent>
    </Dialog>
  )
}
