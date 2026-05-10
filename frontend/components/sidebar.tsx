"use client"

import { useRef } from "react"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { ThemeToggle } from "@/components/theme-toggle"
import { DocumentInfo } from "@/types/chat"
import { Plus, MessageSquare, Trash2, Upload, FileText, Database } from "lucide-react"
import { cn } from "@/lib/utils"

interface SidebarProps {
  sessions: { id: string; title: string }[]
  activeSessionId: string
  onNewChat: () => void
  onSelectSession: (id: string) => void
  documents: DocumentInfo[]
  totalChunks: number
  onUpload: (files: File[]) => void
  onClearDocuments: () => void
  isUploading: boolean
  uploadError?: string
}

export function Sidebar({
  sessions, activeSessionId, onNewChat, onSelectSession,
  documents, totalChunks, onUpload, onClearDocuments, isUploading, uploadError,
}: SidebarProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || [])
    if (files.length > 0) onUpload(files)
    e.target.value = ""
  }

  return (
    <aside className="w-[280px] flex-shrink-0 border-e bg-card flex flex-col h-full">
      {/* Header */}
      <div className="p-4 border-b flex items-center justify-between">
        <div>
          <h1 className="font-bold text-lg bg-gradient-to-l from-sky-500 to-cyan-400 bg-clip-text text-transparent">
            RAGChat AI
          </h1>
          <p className="text-xs text-muted-foreground">חיפוש חכם במסמכים</p>
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
      <ScrollArea className="flex-1 p-2">
        {sessions.length > 0 && (
          <div className="mb-3">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground px-2 py-1">
              היסטוריה
            </p>
            {sessions.map(s => (
              <button
                key={s.id}
                onClick={() => onSelectSession(s.id)}
                className={cn(
                  "w-full text-right px-3 py-2 rounded-lg text-sm flex items-center gap-2 transition-colors",
                  s.id === activeSessionId
                    ? "bg-accent text-accent-foreground font-medium"
                    : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                )}
              >
                <MessageSquare className="w-3.5 h-3.5 shrink-0" />
                <span className="truncate">{s.title}</span>
              </button>
            ))}
          </div>
        )}
      </ScrollArea>

      <Separator />

      {/* Documents section */}
      <div className="p-3 space-y-2">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground px-1">
          מאגר ידע
        </p>

        {/* Stats */}
        <div className="flex items-center gap-2 px-1">
          <Database className="w-3.5 h-3.5 text-muted-foreground" />
          <span className="text-xs text-muted-foreground">
            <span className="font-semibold text-foreground">{totalChunks}</span> קטעים מאונדקסים
          </span>
        </div>

        {/* Document list */}
        {documents.length > 0 && (
          <div className="space-y-1">
            {documents.map((doc, i) => (
              <div key={i} className="flex items-center gap-2 px-1 py-0.5">
                <FileText className="w-3 h-3 text-sky-500 shrink-0" />
                <span className="text-xs truncate text-muted-foreground flex-1">{doc.source}</span>
                <span className="text-[10px] text-muted-foreground">{doc.chunks}</span>
              </div>
            ))}
          </div>
        )}

        {/* Upload button */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          // Backend currently supports: pdf/txt/md
          accept=".pdf,.txt,.md"
          onChange={handleFileChange}
          className="hidden"
        />
        <Button
          variant="outline"
          size="sm"
          className="w-full gap-2 text-xs"
          onClick={() => fileInputRef.current?.click()}
          disabled={isUploading}
        >
          <Upload className="w-3.5 h-3.5" />
          {isUploading ? "...מעלה" : "העלה מסמכים"}
        </Button>

        {uploadError && (
          <p className="text-xs text-destructive px-1 leading-snug">
            {uploadError}
          </p>
        )}

        {/* Clear button */}
        {totalChunks > 0 && (
          <Button
            variant="ghost"
            size="sm"
            className="w-full gap-2 text-xs text-destructive hover:text-destructive"
            onClick={onClearDocuments}
          >
            <Trash2 className="w-3.5 h-3.5" />
            מחק הכל
          </Button>
        )}
      </div>
    </aside>
  )
}
