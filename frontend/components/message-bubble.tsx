"use client"

import { useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import rehypeRaw from "rehype-raw"
import { Message, CitationDetail } from "@/types/chat"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { FileText, Search, MessageCircle, ChevronDown } from "lucide-react"

interface MessageBubbleProps {
  message: Message
}

function escapeHtmlAttr(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;")
    .replace(/\n/g, " ")
}

function injectCitations(content: string, citations?: CitationDetail[]): string {
  const parts = content.split(/(```[\s\S]*?```|`[^`]+`)/g)
  return parts
    .map((part, i) => {
      if (i % 2 === 1) return part
      return part.replace(/\[(\d+(?:\s*,\s*\d+)*)\]/g, (_, nums) => {
        return nums.split(/\s*,\s*/).map((num: string) => {
          const idx = parseInt(num, 10) - 1
          const cite = citations?.[idx]
          const src = escapeHtmlAttr(cite?.source ?? `מקור ${num}`)
          const snippet = escapeHtmlAttr(cite?.snippet ?? "")
          return `<cite title="${src}" data-snippet="${snippet}">${num}</cite>`
        }).join("")
      })
    })
    .join("")
}

// Small pill that shows source name + chunk snippet on hover
function CiteBadge({ children, title, snippet }: { children: React.ReactNode; title?: string; snippet?: string }) {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <sup
            className={cn(
              "relative -top-px inline-flex items-center justify-center align-baseline",
              "mx-0.5 h-[1.15rem] min-w-[1.15rem] rounded-full px-1",
              "bg-muted text-muted-foreground hover:bg-foreground hover:text-background transition-colors",
              "text-[10px] font-medium leading-none tabular-nums",
              "cursor-default select-none"
            )}
          >
            {children}
          </sup>
        </TooltipTrigger>
        {(title || snippet) && (
          <TooltipContent side="top" className="max-w-[350px] border bg-popover p-3 text-popover-foreground shadow-lg">
            <div className="space-y-1.5" dir="rtl">
              {title && (
                <p className="flex items-center gap-1.5 text-xs font-medium">
                  <FileText className="h-3 w-3 shrink-0 text-muted-foreground" />
                  {title}
                </p>
              )}
              {snippet && (
                <p className="line-clamp-3 text-[11px] font-normal leading-relaxed text-muted-foreground">
                  {snippet}…
                </p>
              )}
            </div>
          </TooltipContent>
        )}
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

export function MessageBubble({ message }: MessageBubbleProps) {
  const [showDetails, setShowDetails] = useState(false)
  const [showThinking, setShowThinking] = useState(false)
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

  // Pre-process content: [n] → <cite title="…" data-snippet="…">n</cite>
  const processedContent = injectCitations(message.content, message.citations)

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
            // Citation badge — rendered from <cite title="…" data-snippet="…">n</cite>
            cite: ({ node, children }) => (
              <CiteBadge
                title={(node as any)?.properties?.title}
                snippet={(node as any)?.properties?.dataSnippet}
              >
                {children}
              </CiteBadge>
            ),
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

      {/* Route label · sources toggle · time */}
      {(message.route || hasDetails) && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
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
