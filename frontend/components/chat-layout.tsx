"use client"

import { useState, useRef, useEffect, useCallback } from "react"
import { v4 as uuidv4 } from "uuid"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Sidebar } from "@/components/sidebar"
import { MessageBubble } from "@/components/message-bubble"
import { ThinkingIndicator } from "@/components/thinking-indicator"
import { Message, ChatSession, DocumentInfo, ThinkingStep } from "@/types/chat"
import { sendMessageStreaming, uploadFiles, getDocuments, clearDocuments } from "@/lib/api"
import { Send, Sparkles } from "lucide-react"
import { cn } from "@/lib/utils"

function createSession(): ChatSession {
  return { id: uuidv4(), title: "שיחה חדשה", messages: [], createdAt: new Date() }
}

export function ChatLayout() {
  const [sessions, setSessions] = useState<ChatSession[]>(() => [createSession()])
  const [activeId, setActiveId] = useState<string>(() => "")
  const [input, setInput] = useState("")
  const [isLoading, setIsLoading] = useState(false)
  const [documents, setDocuments] = useState<DocumentInfo[]>([])
  const [totalChunks, setTotalChunks] = useState(0)
  const [isUploading, setIsUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string>("")
  const [thinkingSteps, setThinkingSteps] = useState<ThinkingStep[]>([])
  const [currentNode, setCurrentNode] = useState<string | undefined>(undefined)
  const [streamingAnswer, setStreamingAnswer] = useState("")
  const [isStreaming, setIsStreaming] = useState(false)
  const thinkingStepsRef = useRef<ThinkingStep[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Init active session
  useEffect(() => {
    if (sessions.length > 0 && !activeId) setActiveId(sessions[0].id)
  }, [sessions, activeId])

  const activeSession = sessions.find(s => s.id === activeId)

  const refreshDocuments = useCallback(async () => {
    try {
      const data = await getDocuments()
      setDocuments(data.documents)
      setTotalChunks(data.total_chunks)
    } catch {}
  }, [])

  useEffect(() => { refreshDocuments() }, [refreshDocuments])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [activeSession?.messages, isLoading, thinkingSteps, streamingAnswer])

  const updateSession = (id: string, updater: (s: ChatSession) => ChatSession) => {
    setSessions(prev => prev.map(s => s.id === id ? updater(s) : s))
  }

  const handleSend = async () => {
    const query = input.trim()
    if (!query || isLoading || !activeSession) return
    setInput("")

    const userMsg: Message = {
      id: uuidv4(), role: "user", content: query, timestamp: new Date(),
    }

    const chatHistory = activeSession.messages.map(m => ({
      role: m.role, content: m.content,
    }))

    const sessionTitle = activeSession.title === "שיחה חדשה" ? query.slice(0, 40) : activeSession.title

    updateSession(activeId, s => ({
      ...s,
      title: sessionTitle,
      messages: [...s.messages, userMsg],
    }))

    setIsLoading(true)
    setThinkingSteps([])
    setCurrentNode("starting")
    setStreamingAnswer("")
    setIsStreaming(false)
    thinkingStepsRef.current = []

    try {
      const result = await sendMessageStreaming(
        query,
        chatHistory,
        activeId,
        (step: ThinkingStep) => {
          // Mark previous steps as done, add new step
          setThinkingSteps(prev => {
            const updated = prev.map(s => ({ ...s, status: "done" as const }))
            const newSteps = [...updated, { ...step, status: "done" as const }]
            thinkingStepsRef.current = newSteps
            return newSteps
          })
          setCurrentNode(step.node)
        },
        (token: string) => {
          // First token → switch from thinking indicator to streaming text
          setIsStreaming(true)
          setStreamingAnswer(prev => prev + token)
        },
      )

      // Clear thinking/streaming state
      setCurrentNode(undefined)
      setStreamingAnswer("")
      setIsStreaming(false)

      const assistantMsg: Message = {
        id: uuidv4(),
        role: "assistant",
        content: result.answer,
        sources: result.sources,
        citations: result.citations,
        route: result.route,
        rewritten: result.rewritten,
        fullDocDecision: result.full_doc_decision,
        plan: result.plan,
        thinkingSteps: [...thinkingStepsRef.current],
        timestamp: new Date(),
      }
      updateSession(activeId, s => ({ ...s, messages: [...s.messages, assistantMsg] }))
    } catch (err) {
      setCurrentNode(undefined)
      setStreamingAnswer("")
      setIsStreaming(false)
      const errMsg: Message = {
        id: uuidv4(),
        role: "assistant",
        content: `שגיאה: ${err instanceof Error ? err.message : "נסה שוב"}`,
        timestamp: new Date(),
      }
      updateSession(activeId, s => ({ ...s, messages: [...s.messages, errMsg] }))
    } finally {
      setIsLoading(false)
      setThinkingSteps([])
      setCurrentNode(undefined)
      setStreamingAnswer("")
      setIsStreaming(false)
      thinkingStepsRef.current = []
      setTimeout(() => inputRef.current?.focus(), 0)
    }
  }

  const handleNewChat = () => {
    const session = createSession()
    setSessions(prev => [session, ...prev])
    setActiveId(session.id)
    setInput("")
  }

  const handleUpload = async (files: File[]) => {
    setIsUploading(true)
    setUploadError("")
    try {
      const result = await uploadFiles(files)
      const failed = result.results?.filter(r => r.status !== "ok") ?? []
      if (failed.length > 0) {
        const msg = failed
          .slice(0, 2)
          .map(r => `${r.filename}: ${r.message || "שגיאה"}`)
          .join(" | ")
        setUploadError(msg)
      }
      await refreshDocuments()
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "העלאה נכשלה")
    } finally {
      setIsUploading(false)
    }
  }

  const handleClear = async () => {
    await clearDocuments()
    await refreshDocuments()
  }

  const isEmpty = !activeSession?.messages.length

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background" dir="rtl">
      {/* Sidebar first = RIGHT side in RTL */}
      <Sidebar
        sessions={sessions.map(s => ({ id: s.id, title: s.title }))}
        activeSessionId={activeId}
        onNewChat={handleNewChat}
        onSelectSession={setActiveId}
        documents={documents}
        totalChunks={totalChunks}
        onUpload={handleUpload}
        onClearDocuments={handleClear}
        isUploading={isUploading}
        uploadError={uploadError}
      />

      {/* Main chat area = LEFT side in RTL */}
      <main className="flex-1 flex flex-col min-w-0">
        {/* Messages */}
        <ScrollArea className="flex-1 px-4 py-4">
          {isEmpty ? (
            <div className="h-full flex flex-col items-center justify-center gap-4 text-center py-24">
              <div className="flex gap-2">
                {[0,1,2,3,4].map(i => (
                  <span
                    key={i}
                    className="w-3 h-3 rounded-full bg-sky-400 animate-thinking"
                    style={{ animationDelay: `${i * 0.16}s` }}
                  />
                ))}
              </div>
              <h2 dir="rtl" className="text-2xl font-bold text-foreground">👋 שלום, שמאי</h2>
              <p dir="rtl" className="text-muted-foreground max-w-sm text-sm leading-relaxed">
                מערכת חיפוש חכמה במסמכי שמאות, חוות דעת ודוחות הערכה.
                <br />העלה קבצים מהסיידבר ושאל כל שאלה על הנכסים.
              </p>
              <div className="flex gap-2 flex-wrap justify-center mt-2">
                {[
                  "מה שווי הנכס לפי חוות הדעת?",
                  "אילו פגמים נמצאו בנכס?",
                  "מהי שיטת ההשוואה ששימשה?",
                  "מה עלות הבנייה למ\"ר?",
                  "השווה בין שתי שומות",
                ].map(q => (
                  <button
                    key={q}
                    dir="rtl"
                    onClick={() => { setInput(q); inputRef.current?.focus() }}
                    className="text-xs px-3 py-1.5 rounded-full border border-border bg-card hover:bg-accent transition-colors text-muted-foreground"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="max-w-3xl mx-auto space-y-1 pb-4">
              {activeSession?.messages.map(msg => (
                <MessageBubble key={msg.id} message={msg} />
              ))}
              {isLoading && !isStreaming && (
                <div className="flex justify-end mb-4">
                  <ThinkingIndicator steps={thinkingSteps} currentNode={currentNode} />
                </div>
              )}
              {isLoading && isStreaming && streamingAnswer && (
                <MessageBubble
                  message={{
                    id: "streaming",
                    role: "assistant",
                    content: streamingAnswer,
                    timestamp: new Date(),
                  }}
                />
              )}
              <div ref={bottomRef} />
            </div>
          )}
        </ScrollArea>

        {/* Input area */}
        <div className="border-t p-4 bg-background/95 backdrop-blur">
          <div className="max-w-3xl mx-auto">
            <div className="flex gap-2 items-end">
              <Input
                ref={inputRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend() } }}
                placeholder="שאל שאלה על מסמכי השמאות..."
                className="flex-1 h-11 rounded-xl border-border focus-visible:ring-sky-400 bg-card"
                disabled={isLoading}
              />
              <Button
                onClick={handleSend}
                disabled={!input.trim() || isLoading}
                size="icon"
                className="h-11 w-11 rounded-xl shrink-0"
              >
                {isLoading ? (
                  <Sparkles className="w-4 h-4 animate-spin" />
                ) : (
                  <Send className="w-4 h-4 rotate-180" />
                )}
              </Button>
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}
