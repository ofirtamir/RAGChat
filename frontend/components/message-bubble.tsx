"use client"

import { useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import rehypeRaw from "rehype-raw"
import { Message, CitationDetail } from "@/types/chat"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { FileText, Search, MessageCircle, ChevronDown, ExternalLink, ThumbsUp, ThumbsDown } from "lucide-react"
import { SourceViewer } from "@/components/source-viewer"

interface MessageBubbleProps {
  message: Message
  /** Rate the answer (1 / -1, 0 withdraws). Shown only for answers the server logged. */
  onFeedback?: (rating: 1 | -1 | 0, comment?: string) => Promise<void>
}

/** 👍 / 👎 under an answer; 👎 asks (optionally) what was wrong. */
function FeedbackButtons({ message, onFeedback }: {
  message: Message
  onFeedback: NonNullable<MessageBubbleProps["onFeedback"]>
}) {
  const current = message.feedback?.rating ?? 0
  const [asking, setAsking] = useState(false)
  const [comment, setComment] = useState("")
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)

  const send = async (rating: 1 | -1 | 0, text?: string) => {
    setBusy(true)
    setFailed(false)
    try {
      await onFeedback(rating, text)
      setAsking(false)
    } catch {
      setFailed(true)
    } finally {
      setBusy(false)
    }
  }

  const button = (rating: 1 | -1, Icon: typeof ThumbsUp, label: string) => (
    <button
      type="button"
      disabled={busy}
      aria-label={label}
      title={label}
      aria-pressed={current === rating}
      onClick={() => {
        if (current === rating) return send(0)
        if (rating === -1) return setAsking(true)
        return send(1)
      }}
      className={cn(
        "rounded-full p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
        current === rating && "bg-accent text-foreground",
      )}
    >
      <Icon className="h-3.5 w-3.5" />
    </button>
  )

  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {button(1, ThumbsUp, "התשובה עזרה")}
      {button(-1, ThumbsDown, "התשובה לא עזרה")}
      {failed && <span className="text-[11px] text-red-600">לא נשמר, נסה שוב</span>}
      {asking && (
        <span className="flex w-full items-center gap-1.5 pt-1 sm:w-auto sm:pt-0">
          <input
            autoFocus
            dir="rtl"
            value={comment}
            maxLength={2000}
            onChange={e => setComment(e.target.value)}
            onKeyDown={e => e.key === "Enter" && send(-1, comment)}
            placeholder="מה היה חסר או שגוי? (לא חובה)"
            className="h-7 w-64 rounded-full border bg-background px-3 text-xs outline-none focus:border-ring"
          />
          <button type="button" disabled={busy} onClick={() => send(-1, comment)}
            className="h-7 rounded-full bg-foreground px-3 text-xs text-background">שלח</button>
          <button type="button" onClick={() => setAsking(false)}
            className="h-7 rounded-full px-2 text-xs text-muted-foreground hover:text-foreground">ביטול</button>
        </span>
      )}
    </span>
  )
}

/** [n] / [n, m] → <cite data-num="n">n</cite>; the badge looks the citation up by number. */
function injectCitations(content: string): string {
  const parts = content.split(/(```[\s\S]*?```|`[^`]+`)/g)
  return parts
    .map((part, i) => {
      if (i % 2 === 1) return part
      return part.replace(/\[(\d+(?:\s*,\s*\d+)*)\]/g, (_, nums) =>
        nums.split(/\s*,\s*/).map((num: string) => `<cite data-num="${num}">${num}</cite>`).join("")
      )
    })
    .join("")
}

// Matches snippet_len in src/graph.py — a snippet this long was cut off.
const SNIPPET_LIMIT = 700

/** Numbered pill: hover shows the cited passage, click opens the source document. */
function CiteBadge({ num, citation, onOpen }: {
  num: string
  citation?: CitationDetail
  onOpen: (citation: CitationDetail) => void
}) {
  const title = citation?.source ?? `מקור ${num}`
  const snippet = citation?.snippet ?? ""
  const canOpen = !!(citation?.agent_id && citation.record_key)
  const [tipOpen, setTipOpen] = useState(false)
  const open = () => {
    if (!canOpen) return
    setTipOpen(false)
    onOpen(citation!)
  }

  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip open={tipOpen} onOpenChange={setTipOpen}>
        <TooltipTrigger asChild>
          <sup
            role={canOpen ? "button" : undefined}
            tabIndex={canOpen ? 0 : undefined}
            onClick={open}
            onKeyDown={e => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), open())}
            className={cn(
              "relative -top-px inline-flex items-center justify-center align-baseline",
              "mx-0.5 h-[1.15rem] min-w-[1.15rem] rounded-full px-1",
              "bg-muted text-muted-foreground hover:bg-foreground hover:text-background transition-colors",
              "text-[10px] font-medium leading-none tabular-nums select-none",
              canOpen ? "cursor-pointer" : "cursor-default"
            )}
          >
            {num}
          </sup>
        </TooltipTrigger>
        <TooltipContent
          side="top"
          collisionPadding={12}
          className="flex w-[min(30rem,calc(100vw-2rem))] flex-col border bg-popover p-0 text-popover-foreground shadow-lg"
        >
          <div dir="rtl" className="flex min-h-0 flex-col">
            <p className="flex items-start gap-1.5 border-b px-4 py-3 text-[13px] font-semibold leading-snug">
              <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              {title}
            </p>
            {snippet && (
              <p className="scrollbar-thin max-h-[min(18rem,45vh)] overflow-y-auto whitespace-pre-line px-4 py-3 text-[12.5px] font-normal leading-relaxed text-muted-foreground">
                {snippet}{snippet.length >= SNIPPET_LIMIT - 10 && "…"}
              </p>
            )}
            {canOpen && (
              <button
                type="button"
                onClick={open}
                className="flex items-center gap-1.5 border-t px-4 py-2.5 text-[12.5px] font-medium text-blue-600 transition-colors hover:bg-accent dark:text-blue-400"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                פתח את המסמך
                {citation?.page != null && (
                  <span className="font-normal text-muted-foreground">· עמוד {citation.page + 1}</span>
                )}
              </button>
            )}
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

/** Quiet label describing how the answer was produced. */
function RouteLabel({ message }: { message: Message }) {
  if (!message.route) return null
  const [Icon, label] = !message.route.needs_retrieval
    ? [MessageCircle, "שיחה חופשית"]
    : message.fullDocDecision?.needs_full_document
      ? [FileText, "מסמך מלא"]
      : [Search, "אחזור מסמכים"]
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
      <Icon className="h-3.5 w-3.5" />
      {label}
    </span>
  )
}

export function MessageBubble({ message, onFeedback }: MessageBubbleProps) {
  const [showDetails, setShowDetails] = useState(false)
  const [showThinking, setShowThinking] = useState(false)
  const [openCitation, setOpenCitation] = useState<CitationDetail | null>(null)
  const isUser = message.role === "user"
  const hasDetails = message.route || message.sources?.length || message.rewritten || message.fullDocDecision
  const hasThinking = message.thinkingSteps && message.thinkingSteps.length > 0
  const sourceCount = message.sources?.length ?? 0
  const time = message.timestamp.toLocaleTimeString("he-IL", { hour: "2-digit", minute: "2-digit" })

  if (isUser) {
    return (
      <div className="group flex w-full flex-col items-end gap-1">
        <div
          dir="rtl"
          className="max-w-[85%] whitespace-pre-wrap break-words rounded-3xl bg-muted px-5 py-2.5 text-right text-[15px] leading-7 sm:max-w-[70%]"
        >
          {message.content}
        </div>
        <span className="px-2 text-[11px] text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100">
          {time}
        </span>
      </div>
    )
  }

  // Pre-process content: [n] → <cite data-num="n">n</cite>
  const processedContent = injectCitations(message.content)

  return (
    <div className="flex w-full flex-col gap-2">
      {/* Thinking steps toggle */}
      {hasThinking && (
        <div>
          <button
            onClick={() => setShowThinking(!showThinking)}
            className="group inline-flex items-center gap-1 text-[13px] text-muted-foreground transition-colors hover:text-foreground"
          >
            תהליך חשיבה
            <ChevronDown className={cn(
              "h-3.5 w-3.5 transition-transform duration-200",
              showThinking && "rotate-180"
            )} />
          </button>

          <div className={cn(
            "overflow-hidden transition-all duration-300 ease-in-out",
            showThinking ? "max-h-[400px] opacity-100" : "max-h-0 opacity-0"
          )}>
            <ol className="mt-2 space-y-1.5 border-s-2 border-border ps-4">
              {message.thinkingSteps!.map((step, index) => (
                <li
                  key={step.node}
                  className="animate-fadeIn text-[13px] leading-snug"
                  style={{ animationDelay: `${index * 40}ms` }}
                >
                  <span className="text-muted-foreground">{step.label}</span>
                  {step.detail && (
                    <span className="block truncate text-xs text-muted-foreground/70">{step.detail}</span>
                  )}
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}

      {/* Answer — plain text, no bubble */}
      <div dir="rtl" className="prose prose-neutral max-w-none text-right text-[15px] leading-7 text-foreground dark:prose-invert prose-rtl">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[rehypeRaw]}
          components={{
            // Citation badge — rendered from <cite data-num="n">n</cite>
            cite: ({ node }) => {
              const num = String((node as any)?.properties?.dataNum ?? "")
              return (
                <CiteBadge
                  num={num}
                  citation={message.citations?.[parseInt(num, 10) - 1]}
                  onOpen={setOpenCitation}
                />
              )
            },
            h1: ({ children }) => (
              <h1 className="mb-3 mt-6 text-xl font-semibold text-foreground first:mt-0">{children}</h1>
            ),
            h2: ({ children }) => (
              <h2 className="mb-2 mt-6 text-lg font-semibold text-foreground first:mt-0">{children}</h2>
            ),
            h3: ({ children }) => (
              <h3 className="mb-2 mt-5 text-base font-semibold text-foreground first:mt-0">{children}</h3>
            ),
            p: ({ children }) => (
              <p className="mb-4 leading-7 last:mb-0">{children}</p>
            ),
            strong: ({ children }) => (
              <strong className="font-semibold text-foreground">{children}</strong>
            ),
            em: ({ children }) => (
              <em className="italic">{children}</em>
            ),
            ul: ({ children }) => (
              <ul className="mb-4 list-disc space-y-1.5 ps-6 marker:text-muted-foreground">{children}</ul>
            ),
            ol: ({ children }) => (
              <ol className="mb-4 list-decimal space-y-1.5 ps-6 marker:text-muted-foreground">{children}</ol>
            ),
            li: ({ children }) => (
              <li className="leading-7 ps-1">{children}</li>
            ),
            code: ({ className, children, ...props }) => {
              const isBlock = className?.includes("language-")
              if (isBlock) {
                return (
                  <pre dir="ltr" className="my-4 overflow-x-auto rounded-xl bg-muted p-4 text-[13px]">
                    <code className={className} {...props}>{children}</code>
                  </pre>
                )
              }
              return (
                <code className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[13px] before:content-none after:content-none" {...props}>
                  {children}
                </code>
              )
            },
            pre: ({ children }) => <>{children}</>,
            table: ({ children }) => (
              <div className="my-4 overflow-x-auto rounded-xl border">
                <table className="my-0 w-full border-collapse text-sm">{children}</table>
              </div>
            ),
            thead: ({ children }) => (
              <thead className="bg-muted/60">{children}</thead>
            ),
            th: ({ children }) => (
              <th className="border-b px-4 py-2.5 text-right font-medium text-foreground">{children}</th>
            ),
            td: ({ children }) => (
              <td className="border-b px-4 py-2.5 text-right">{children}</td>
            ),
            tr: ({ children }) => (
              <tr className="[&:last-child>td]:border-0">{children}</tr>
            ),
            blockquote: ({ children }) => (
              <blockquote className="my-4 border-s-[3px] border-border ps-4 font-normal not-italic text-muted-foreground [&_p]:before:content-none [&_p]:after:content-none">
                {children}
              </blockquote>
            ),
            hr: () => <hr className="my-6 border-border" />,
            a: ({ href, children }) => (
              <a href={href} target="_blank" rel="noopener noreferrer" className="font-normal text-blue-600 underline decoration-blue-600/30 underline-offset-2 hover:decoration-blue-600 dark:text-blue-400 dark:decoration-blue-400/30">
                {children}
              </a>
            ),
          }}
        >
          {processedContent}
        </ReactMarkdown>
      </div>

      <SourceViewer citation={openCitation} onClose={() => setOpenCitation(null)} />

      {/* Route label · sources toggle · time */}
      {(message.route || hasDetails || (onFeedback && message.questionId)) && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {onFeedback && message.questionId && <FeedbackButtons message={message} onFeedback={onFeedback} />}
          <RouteLabel message={message} />
          {hasDetails && (
            <button
              onClick={() => setShowDetails(!showDetails)}
              className={cn(
                "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
                showDetails && "bg-accent text-foreground"
              )}
            >
              {sourceCount > 0 ? `${sourceCount} מקורות` : "פרטים"}
              <ChevronDown className={cn("h-3 w-3 transition-transform", showDetails && "rotate-180")} />
            </button>
          )}
          <span className="text-[11px] text-muted-foreground/80">{time}</span>
        </div>
      )}

      {/* Expandable details */}
      {showDetails && (
        <div className="space-y-4 rounded-2xl border p-4 text-xs animate-fadeIn">
          {message.sources && message.sources.length > 0 && (
            <div>
              <p className="mb-2 font-medium text-muted-foreground">מקורות</p>
              <div className="flex flex-wrap gap-1.5">
                {message.sources.map((src, i) => (
                  <span key={i} className="inline-flex max-w-full items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-foreground/80">
                    <span className="flex h-4 min-w-4 items-center justify-center rounded-full bg-background px-1 text-[10px] tabular-nums text-muted-foreground">{i + 1}</span>
                    <span className="truncate">{src}</span>
                  </span>
                ))}
              </div>
            </div>
          )}

          {message.fullDocDecision?.needs_full_document && message.fullDocDecision.target_sources.length > 0 && (
            <div>
              <p className="mb-2 font-medium text-muted-foreground">מסמכים שנאחזרו במלואם</p>
              <div className="flex flex-wrap gap-1.5">
                {message.fullDocDecision.target_sources.map((src, i) => (
                  <span key={i} className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-foreground/80">
                    <FileText className="h-3 w-3 shrink-0 text-muted-foreground" />
                    {src}
                  </span>
                ))}
              </div>
            </div>
          )}

          {message.rewritten && !message.fullDocDecision?.needs_full_document && (
            <div>
              <p className="mb-2 font-medium text-muted-foreground">שאילתת חיפוש</p>
              <div className="flex flex-wrap gap-1.5">
                {message.rewritten.rewritten_queries.map((q, i) => (
                  <code key={i} className="rounded-md bg-muted px-2 py-0.5 text-muted-foreground">{q}</code>
                ))}
              </div>
            </div>
          )}

          {message.plan && message.route?.needs_retrieval && (
            <div>
              <p className="mb-1 font-medium text-muted-foreground">
                תוכנית שאילתה
                {message.plan.is_complex && (
                  <span className="ms-1.5 rounded-full bg-muted px-2 py-0.5 text-foreground/80">מורכב</span>
                )}
              </p>
              <p className="leading-relaxed text-muted-foreground">{message.plan.reasoning}</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
