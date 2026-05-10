"use client"

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { DocumentInfo } from "@/types/chat"
import { FileText, Database, Trash2, Upload } from "lucide-react"

interface DocumentsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  documents: DocumentInfo[]
  totalChunks: number
  onUpload: (files: File[]) => void
  onClearDocuments: () => void
  isUploading: boolean
  uploadError?: string
}

function formatChars(chars: number): string {
  if (chars >= 1000) return `${(chars / 1000).toFixed(1)}k תווים`
  return `${chars} תווים`
}

function getFileExtension(filename: string): string {
  return filename.split(".").pop()?.toLowerCase() ?? ""
}

function FileIcon({ filename }: { filename: string }) {
  const ext = getFileExtension(filename)
  const colorMap: Record<string, string> = {
    pdf: "text-red-500",
    txt: "text-gray-500",
    md: "text-purple-500",
  }
  return <FileText className={`w-4 h-4 shrink-0 ${colorMap[ext] ?? "text-sky-500"}`} />
}

export function DocumentsDialog({
  open,
  onOpenChange,
  documents,
  totalChunks,
  onUpload,
  onClearDocuments,
  isUploading,
  uploadError,
}: DocumentsDialogProps) {
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || [])
    if (files.length > 0) onUpload(files)
    e.target.value = ""
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md w-full" dir="rtl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-right">
            <Database className="w-5 h-5 text-sky-500" />
            מסמכי שמאות
          </DialogTitle>
          <DialogDescription className="text-right">
            {documents.length === 0
              ? "לא הועלו קבצים עדיין. העלה חוות דעת, דוחות הערכה או כל מסמך שמאות."
              : `${documents.length} קבצים · ${totalChunks} קטעים מאונדקסים`}
          </DialogDescription>
        </DialogHeader>

        {/* Document list */}
        {documents.length > 0 && (
          <ScrollArea className="max-h-64 -mx-1 px-1">
            <div className="space-y-1">
              {documents.map((doc, i) => (
                <div
                  key={i}
                  className="flex items-center gap-3 px-3 py-2.5 rounded-lg bg-muted/50 hover:bg-muted transition-colors"
                >
                  <FileIcon filename={doc.source} />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate text-right">{doc.source}</p>
                    <p className="text-[11px] text-muted-foreground text-right">
                      {doc.chunks} קטעים · {formatChars(doc.chars)}
                    </p>
                  </div>
                  <span className="text-[10px] font-mono bg-background border rounded px-1.5 py-0.5 text-muted-foreground shrink-0">
                    {getFileExtension(doc.source).toUpperCase()}
                  </span>
                </div>
              ))}
            </div>
          </ScrollArea>
        )}

        {uploadError && (
          <p className="text-xs text-destructive leading-snug text-right">{uploadError}</p>
        )}

        {/* Actions */}
        <div className="flex gap-2 pt-1">
          <label className="flex-1">
            <input
              type="file"
              multiple
              accept=".pdf,.txt,.md"
              onChange={handleFileChange}
              className="hidden"
              disabled={isUploading}
            />
            <Button
              variant="default"
              size="sm"
              className="w-full gap-2"
              disabled={isUploading}
              onClick={(e) => {
                // trigger the hidden input inside the label
                const input = (e.currentTarget.parentElement as HTMLLabelElement)
                  ?.querySelector("input")
                input?.click()
              }}
            >
              <Upload className="w-4 h-4" />
              {isUploading ? "...מעלה" : "העלה חוות דעת / שומות"}
            </Button>
          </label>

          {totalChunks > 0 && (
            <Button
              variant="destructive"
              size="sm"
              className="gap-2"
              onClick={() => {
                onClearDocuments()
                onOpenChange(false)
              }}
            >
              <Trash2 className="w-4 h-4" />
              מחק הכל
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
