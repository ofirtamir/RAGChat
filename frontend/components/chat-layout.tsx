"use client"

import { useState, useRef, useEffect, useCallback } from "react"
import { useSession } from "next-auth/react"
import { v4 as uuidv4 } from "uuid"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Sidebar } from "@/components/sidebar"
import { MessageBubble } from "@/components/message-bubble"
import { ThinkingIndicator } from "@/components/thinking-indicator"
import { Message, ChatSession, DocumentInfo, ThinkingStep } from "@/types/chat"
import {
  sendMessageStreaming,
  uploadFiles,
  getDocuments,
  clearDocuments,
  listSessions,
  getSession,
  upsertSession,
  deleteSession,
} from "@/lib/api"
import { Send, Sparkles } from "lucide-react"
import { cn } from "@/lib/utils"

const LOCAL_STORAGE_KEY = "ragchat_sessions"
const LOCAL_STORAGE_ACTIVE_KEY = "ragchat_active_session"

function createSession(): ChatSession {
  return { id: uuidv4(), title: "שיחה חדשה", messages: [], createdAt: new Date() }
}

function saveSessionsToLocalStorage(sessions: ChatSession[], activeId: string) {
  try {
    const toSave = sessions.filter(s => s.messages.length > 0)
    if (toSave.length === 0) {
      // Clear localStorage when no sessions have messages (e.g. all deleted)
      localStorage.removeItem(LOCAL_STORAGE_KEY)
      localStorage.removeItem(LOCAL_STORAGE_ACTIVE_KEY)
      return
    }
    const serializable = toSave.map(s => ({
      ...s,
      createdAt: s.createdAt.toISOString(),
      messages: s.messages.map(m => ({
        ...m,
        timestamp: m.timestamp instanceof Date ? m.timestamp.toISOString() : m.timestamp,
      })),
    }))
    localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(serializable))
    localStorage.setItem(LOCAL_STORAGE_ACTIVE_KEY, activeId)
  } catch {}
}

function loadSessionsFromLocalStorage(): { sessions: ChatSession[]; activeId: string } | null {
  try {
    const raw = localStorage.getItem(LOCAL_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Array<Record<string, unknown>>
    const sessions: ChatSession[] = parsed.map((s: Record<string, unknown>) => ({
      id: s.id as string,
      title: s.title as string,
      createdAt: new Date(s.createdAt as string),
      messages: (s.messages as Array<Record<string, unknown>>).map((m: Record<string, unknown>) => ({
        ...m,
        timestamp: new Date(m.timestamp as string),
      })) as Message[],
    }))
    const activeId = localStorage.getItem(LOCAL_STORAGE_ACTIVE_KEY) || ""
    return sessions.length > 0 ? { sessions, activeId } : null
  } catch {
    return null
  }
}

export function ChatLayout() {
  const { data: authSession, status: authStatus } = useSession()
  const userId = authSession?.user?.id
  const authToken = authSession?.idToken
  const [sessions, setSessions] = useState<ChatSession[]>(() => [createSession()])
  const [activeId, setActiveId] = useState<string>(() => "")
  const [historyLoaded, setHistoryLoaded] = useState(false)
  const [localLoaded, setLocalLoaded] = useState(false)
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

  // Load sessions from localStorage on mount (client-only, runs once)
  useEffect(() => {
    const cached = loadSessionsFromLocalStorage()
    if (cached && cached.sessions.some(s => s.messages.length > 0)) {
      const restoredSessions = [createSession(), ...cached.sessions]
      setSessions(restoredSessions)
      // Ensure activeId points to a session that actually exists in the restored list
      const validId = restoredSessions.find(s => s.id === cached.activeId)
        ? cached.activeId
        : cached.sessions[0]?.id ?? restoredSessions[0].id
      setActiveId(validId)
    }
    setLocalLoaded(true)
  }, [])

  // Persist sessions to localStorage on every change
  useEffect(() => {
    if (!localLoaded) return
    if (sessions.length > 0) {
      // Save activeId only if it points to a session with messages,
      // otherwise save the first session with messages (so reload picks a valid session).
      const sessionsWithMessages = sessions.filter(s => s.messages.length > 0)
      const validActiveId =
        sessionsWithMessages.find(s => s.id === activeId)?.id ??
        sessionsWithMessages[0]?.id ??
        activeId
      saveSessionsToLocalStorage(sessions, validActiveId)
    }
  }, [sessions, activeId, localLoaded])

  // Load persisted history when the user signs in
  useEffect(() => {
    if (authStatus !== "authenticated" || !userId || !authToken || historyLoaded) return
    let cancelled = false
    ;(async () => {
      try {
        const summaries = await listSessions(authToken)
        if (cancelled) return
        if (summaries.length === 0) {
          setHistoryLoaded(true)
          return
        }
        // Fetch each session's full messages in parallel.
        const fulls = await Promise.all(
          summaries.map(s => getSession(s.id, authToken).catch(() => null)),
        )
        if (cancelled) return
        const loaded: ChatSession[] = fulls
          .filter((s): s is NonNullable<typeof s> => Boolean(s))
          .map(s => ({
            id: s.id,
            title: s.title,
            createdAt: new Date(s.created_at),
            messages: (s.messages as Message[]).map(m => ({
              ...m,
              timestamp: m.timestamp ? new Date(m.timestamp as unknown as string) : new Date(),
            })),
          }))
        if (loaded.length > 0) {
          setSessions([createSession(), ...loaded])
          setActiveId(loaded[0].id)
        }
        setHistoryLoaded(true)
      } catch (err) {
        console.error("Failed to load chat history:", err)
        setHistoryLoaded(true)
      }
    })()
    return () => { cancelled = true }
  }, [authStatus, userId, authToken, historyLoaded])

  const activeSession = sessions.find(s => s.id === activeId)

  const refreshDocuments = useCallback(async () => {
    if (!authToken) return
    try {
      const data = await getDocuments(authToken)
      setDocuments(data.documents)
      setTotalChunks(data.total_chunks)
    } catch {}
  }, [authToken])

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
        authToken,
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

      // Persist to backend (best-effort — never blocks the UI)
      if (authToken) {
        const finalMessages = [...activeSession.messages, userMsg, assistantMsg]
        upsertSession({
          id: activeId,
          title: sessionTitle,
          messages: finalMessages,
        }, authToken).catch(err => console.error("Failed to save session:", err))
      }
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

  const handleDeleteSession = async (id: string) => {
    // Optimistic UI: remove immediately, fire backend call in the background.
    setSessions(prev => {
      const filtered = prev.filter(s => s.id !== id)
      if (filtered.length === 0) {
        const fresh = createSession()
        setActiveId(fresh.id)
        return [fresh]
      }
      if (id === activeId) setActiveId(filtered[0].id)
      return filtered
    })
    if (authToken) {
      try {
        await deleteSession(id, authToken)
      } catch (err) {
        console.error("Failed to delete session on server:", err)
      }
    }
  }

  const handleUpload = async (files: File[]) => {
    if (!authToken) {
      setUploadError("נא להתחבר שוב")
      return
    }
    setIsUploading(true)
    setUploadError("")
    try {
      const result = await uploadFiles(files, authToken)
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
    if (!authToken) return
    await clearDocuments(authToken)
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
        onDeleteSession={handleDeleteSession}
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
