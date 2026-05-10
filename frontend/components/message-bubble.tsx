"use client"

import { useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import rehypeRaw from "rehype-raw"
import { Message } from "@/types/chat"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { FileText, Search, MessageCircle, ChevronDown, ChevronUp, Sparkles } from "lucide-react"

interface MessageBubbleProps {
  message: Message
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const [showDetails, setShowDetails] = useState(false)
  const [showThinking, setShowThinking] = useState(false)
  const isUser = message.role === "user"
  const hasDetails = message.route || message.sources?.length || message.rewritten || message.fullDocDecision
  const hasThinking = message.thinkingSteps && message.thinkingSteps.length > 0

  const routeBadge = () => {
    if (!message.route) return null
    if (!message.route.needs_retrieval) {
      return (
        <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-green-50 text-green-700 border border-green-200 dark:bg-green-950 dark:text-green-300 dark:border-green-800">
          <MessageCircle className="w-3 h-3" />
          שיחה חופשית
        </span>
      )
    }
    if (message.fullDocDecision?.needs_full_document) {
      return (
        <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-800">
          <FileText className="w-3 h-3" />
          מסמך מלא
        </span>
      )
    }
    return (
      <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-sky-50 text-sky-700 border border-sky-200 dark:bg-sky-950 dark:text-sky-300 dark:border-sky-800">
        <Search className="w-3 h-3" />
        אחזור מסמכים
      </span>
    )
  }

  return (
    <div className={cn("flex w-full mb-4", isUser ? "justify-start" : "justify-end")}>
      <div className={cn("max-w-[80%] flex flex-col gap-1", isUser ? "items-start" : "items-end")}>

        {/* Thinking steps toggle (Gemini-style, above the bubble) */}
        {!isUser && hasThinking && (
          <div className="mb-0.5 w-full">
            <button
              onClick={() => setShowThinking(!showThinking)}
              className="group flex items-center gap-2 py-1 text-xs transition-colors hover:text-foreground"
            >
              <div className="relative flex items-center justify-center w-5 h-5 shrink-0">
                <Sparkles className="w-3.5 h-3.5 text-sky-500/70 group-hover:text-sky-500 transition-colors" />
              </div>
              <span className="text-muted-foreground group-hover:text-foreground transition-colors">
                תהליך חשיבה
              </span>
              <ChevronDown className={cn(
                "w-3.5 h-3.5 text-muted-foreground/60 transition-transform duration-200",
                showThinking && "rotate-180"
              )} />
            </button>

            {/* Thinking steps expanded */}
            <div className={cn(
              "overflow-hidden transition-all duration-300 ease-in-out",
              showThinking ? "max-h-[400px] opacity-100" : "max-h-0 opacity-0"
            )}>
              <div className="me-3 border-e-2 border-sky-200/60 dark:border-sky-800/40 pe-3 py-1 space-y-1 mb-2">
                {message.thinkingSteps!.map((step, index) => (
                  <div
                    key={step.node}
                    className="relative flex items-start gap-2 py-0.5 animate-fadeIn"
                    style={{ animationDelay: `${index * 40}ms` }}
                  >
                    {/* Timeline dot */}
                    <div className="absolute -end-[0.94rem] top-[0.45rem]">
                      <span className="w-1.5 h-1.5 rounded-full bg-sky-400/60 block" />
                    </div>

                    <span className="text-xs leading-none mt-0.5 shrink-0">{step.icon}</span>
                    <div className="min-w-0 flex-1">
                      <span className="text-[12px] text-muted-foreground leading-snug">{step.label}</span>
                      {step.detail && (
                        <span className="text-[10px] text-muted-foreground/50 block truncate mt-0.5">
                          {step.detail}
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Bubble */}
        <div
          dir="rtl"
          className={cn(
            "px-4 py-3 rounded-2xl text-sm leading-relaxed text-right",
            isUser
              ? "bg-primary text-primary-foreground rounded-tr-sm"
              : "bg-muted text-foreground rounded-tl-sm border"
          )}
        >
          {isUser ? (
            <span className="whitespace-pre-wrap">{message.content}</span>
          ) : (
            <div className="prose prose-sm dark:prose-invert max-w-none prose-rtl">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeRaw]}
                components={{
                  h1: ({ children }) => (
                    <h1 className="text-lg font-bold mt-3 mb-2 text-foreground">{children}</h1>
                  ),
                  h2: ({ children }) => (
                    <h2 className="text-base font-bold mt-3 mb-1.5 text-foreground">{children}</h2>
                  ),
                  h3: ({ children }) => (
                    <h3 className="text-sm font-bold mt-2 mb-1 text-foreground">{children}</h3>
                  ),
                  p: ({ children }) => (
                    <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>
                  ),
                  strong: ({ children }) => (
                    <strong className="font-bold text-foreground">{children}</strong>
                  ),
                  em: ({ children }) => (
                    <em className="italic">{children}</em>
                  ),
                  ul: ({ children }) => (
                    <ul className="list-disc list-inside mb-2 space-y-0.5 me-2">{children}</ul>
                  ),
                  ol: ({ children }) => (
                    <ol className="list-decimal list-inside mb-2 space-y-0.5 me-2">{children}</ol>
                  ),
                  li: ({ children }) => (
                    <li className="leading-relaxed">{children}</li>
                  ),
                  code: ({ className, children, ...props }) => {
                    const isBlock = className?.includes("language-")
                    if (isBlock) {
                      return (
                        <pre dir="ltr" className="bg-background/50 border rounded-lg p-3 my-2 overflow-x-auto text-xs">
                          <code className={className} {...props}>{children}</code>
                        </pre>
                      )
                    }
                    return (
                      <code className="bg-background/50 border rounded px-1.5 py-0.5 text-xs font-mono" {...props}>
                        {children}
                      </code>
                    )
                  },
                  pre: ({ children }) => <>{children}</>,
                  table: ({ children }) => (
                    <div className="overflow-x-auto my-2 rounded-lg border">
                      <table className="w-full text-xs border-collapse">{children}</table>
                    </div>
                  ),
                  thead: ({ children }) => (
                    <thead className="bg-background/50">{children}</thead>
                  ),
                  th: ({ children }) => (
                    <th className="border-b px-3 py-2 text-right font-semibold text-foreground">{children}</th>
                  ),
                  td: ({ children }) => (
                    <td className="border-b px-3 py-2 text-right">{children}</td>
                  ),
                  tr: ({ children }) => (
                    <tr className="hover:bg-background/30 transition-colors">{children}</tr>
                  ),
                  blockquote: ({ children }) => (
                    <blockquote className="border-e-4 border-sky-300 dark:border-sky-700 pe-3 me-2 my-2 text-muted-foreground italic">
                      {children}
                    </blockquote>
                  ),
                  hr: () => <hr className="my-3 border-border" />,
                  a: ({ href, children }) => (
                    <a href={href} target="_blank" rel="noopener noreferrer" className="text-sky-600 dark:text-sky-400 underline underline-offset-2 hover:text-sky-500">
                      {children}
                    </a>
                  ),
                }}
              >
                {message.content}
              </ReactMarkdown>
            </div>
          )}
        </div>

        {/* Route badge + details toggle */}
        {!isUser && (
          <div className="flex items-center gap-2 flex-wrap">
            {routeBadge()}
            {hasDetails && (
              <Button
                variant="ghost"
                size="sm"
                className="h-6 px-2 text-xs text-muted-foreground"
                onClick={() => setShowDetails(!showDetails)}
              >
                {showDetails ? <ChevronUp className="w-3 h-3 me-1" /> : <ChevronDown className="w-3 h-3 me-1" />}
                {showDetails ? "הסתר" : "פרטים"}
              </Button>
            )}
          </div>
        )}

        {/* Expandable details */}
        {!isUser && showDetails && (
          <div className="w-full rounded-xl border bg-card p-3 space-y-3 text-xs">
            {message.sources && message.sources.length > 0 && (
              <div>
                <p className="font-semibold text-muted-foreground mb-1.5">מקורות</p>
                <div className="flex flex-wrap gap-1.5">
                  {message.sources.map((src, i) => (
                    <Badge key={i} variant="source">{src}</Badge>
                  ))}
                </div>
              </div>
            )}

            {message.fullDocDecision?.needs_full_document && message.fullDocDecision.target_sources.length > 0 && (
              <div>
                <p className="font-semibold text-muted-foreground mb-1.5">מסמכים שנאחזרו במלואם</p>
                <div className="flex flex-wrap gap-1.5">
                  {message.fullDocDecision.target_sources.map((src, i) => (
                    <Badge key={i} variant="outline" className="border-amber-300 text-amber-700 dark:text-amber-300">{src}</Badge>
                  ))}
                </div>
              </div>
            )}

            {message.rewritten && !message.fullDocDecision?.needs_full_document && (
              <div>
                <p className="font-semibold text-muted-foreground mb-1.5">שאילתת חיפוש</p>
                <div className="flex flex-wrap gap-1.5">
                  {message.rewritten.rewritten_queries.map((q, i) => (
                    <code key={i} className="px-2 py-0.5 rounded bg-muted text-muted-foreground">{q}</code>
                  ))}
                </div>
              </div>
            )}

            {message.plan && message.route?.needs_retrieval && (
              <div>
                <p className="font-semibold text-muted-foreground mb-1">
                  תוכנית שאילתה
                  {message.plan.is_complex && (
                    <span className="me-1.5 px-1.5 py-0.5 rounded bg-orange-100 text-orange-700 dark:bg-orange-950 dark:text-orange-300">
                      מורכב
                    </span>
                  )}
                </p>
                <p className="text-muted-foreground">{message.plan.reasoning}</p>
              </div>
            )}
          </div>
        )}

        {/* Timestamp */}
        <span className="text-[10px] text-muted-foreground px-1">
          {message.timestamp.toLocaleTimeString("he-IL", { hour: "2-digit", minute: "2-digit" })}
        </span>
      </div>
    </div>
  )
}
