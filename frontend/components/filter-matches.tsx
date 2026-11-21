"use client"

import { useEffect, useState } from "react"
import { useSession } from "next-auth/react"
import { ChevronDown, FileText, Loader2 } from "lucide-react"
import { AgentFilters, getMatchingDocuments, MatchingDocuments } from "@/lib/api"
import { SourceViewer } from "@/components/source-viewer"
import { CitationDetail, FilterCondition } from "@/types/chat"
import { cn } from "@/lib/utils"

const LIST_LIMIT = 300
const DEBOUNCE_MS = 350

/**
 * Under the filter panel: how many decisions the chosen filters leave — with
 * the filters found in the last question (`found`) — a list of them on
 * request (newest first), and a click opens the document.
 */
export function FilterMatches({ agentId, filters, found }: {
  agentId: string
  filters: AgentFilters
  found?: FilterCondition[]
}) {
  const { data: session } = useSession()
  const token = session?.idToken
  const [data, setData] = useState<MatchingDocuments | null>(null)
  const [loading, setLoading] = useState(true)
  const [showList, setShowList] = useState(false)
  const [opened, setOpened] = useState<CitationDetail | null>(null)
  const filtersKey = JSON.stringify(filters)
  const foundKey = JSON.stringify(found ?? [])

  useEffect(() => {
    const ctrl = new AbortController()
    setLoading(true)
    const timer = setTimeout(() => {
      getMatchingDocuments(agentId, JSON.parse(filtersKey), token, showList ? LIST_LIMIT : 0, ctrl.signal,
                           JSON.parse(foundKey))
        .then(d => setData(d))
        .catch(() => { if (!ctrl.signal.aborted) setData(null) })
        .finally(() => { if (!ctrl.signal.aborted) setLoading(false) })
    }, DEBOUNCE_MS)
    return () => { clearTimeout(timer); ctrl.abort() }
  }, [agentId, filtersKey, foundKey, token, showList])

  if (!loading && (!data || data.total === 0)) return null

  const summary = !data
    ? "סופר החלטות…"
    : data.count === data.total
      ? `כל ${data.total.toLocaleString()} ההחלטות במאגר`
      : data.count === 0
        ? "אין החלטות שמתאימות לסינון"
        : `${data.count.toLocaleString()} החלטות מתאימות מתוך ${data.total.toLocaleString()}`
  const withFound = Boolean(found?.length) && data && data.count > 0

  return (
    <div className="mt-3 flex min-h-0 flex-col border-t pt-3">
      <div className="flex shrink-0 items-center gap-2">
        <p className={cn("flex flex-1 items-center gap-1.5 text-sm", loading && data && "opacity-60")} aria-live="polite">
          {loading && <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />}
          <span className={cn(data?.count === 0 && "text-destructive")}>{summary}</span>
          {withFound && <span className="text-xs text-blue-700 dark:text-blue-300">· כולל מה שזוהה מהשאלה</span>}
        </p>
        {data && data.count > 0 && (
          <button
            type="button"
            onClick={() => setShowList(s => !s)}
            className="flex items-center gap-1 rounded-full border px-3 py-1 text-xs transition-colors hover:bg-accent"
            aria-expanded={showList}
          >
            {showList ? "הסתרת הרשימה" : "הצגת ההחלטות"}
            <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", showList && "rotate-180")} />
          </button>
        )}
      </div>

      {showList && data && data.count > 0 && (
        <>
          <ul className="scrollbar-thin mt-2 min-h-[7.5rem] divide-y overflow-y-auto rounded-xl border">
            {data.documents.map(d => (
              <li key={d.key}>
                <button
                  type="button"
                  onClick={() => setOpened({ source: d.title, snippet: "", agent_id: agentId, record_key: d.key, page: null })}
                  className="flex w-full items-start gap-2 px-3 py-2 text-right transition-colors hover:bg-accent"
                >
                  <FileText className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1">
                    <span className="line-clamp-2 text-sm leading-snug">{d.title}</span>
                    {d.date && <span className="text-xs text-muted-foreground">{d.date}</span>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {data.count > data.documents.length && data.documents.length > 0 && (
            <p className="mt-1.5 shrink-0 text-xs text-muted-foreground">
              מוצגות {data.documents.length.toLocaleString()} ההחלטות החדשות ביותר. אפשר לצמצם עם מסננים נוספים.
            </p>
          )}
        </>
      )}

      <SourceViewer citation={opened} onClose={() => setOpened(null)} />
    </div>
  )
}
