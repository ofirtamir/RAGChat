"use client"

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { DocumentInfo } from "@/types/chat"
import { FileText, Trash2, Upload } from "lucide-react"
import { useMe } from "@/lib/use-me"

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
    pdf: "text-red-500/80",
    md: "text-violet-500/80",
  }
  return (
    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-muted">
      <FileText className={`h-4 w-4 ${colorMap[ext] ?? "text-muted-foreground"}`} />
    </span>
  )
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
  const me = useMe()
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || [])
    if (files.length > 0) onUpload(files)
    e.target.value = ""
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-full max-w-md gap-5" dir="rtl">
        <DialogHeader>
          <DialogTitle className="text-right text-lg font-semibold">
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
          <div className="scrollbar-thin -mx-2 max-h-72 overflow-y-auto px-2">
            <div className="space-y-0.5">
              {documents.map((doc, i) => (
                <div
                  key={i}
                  className="flex items-center gap-3 rounded-xl px-2 py-2 transition-colors hover:bg-muted/60"
                >
                  <FileIcon filename={doc.source} />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate text-right">{doc.source}</p>
                    <p className="text-xs text-muted-foreground text-right">
                      {doc.chunks} קטעים · {formatChars(doc.chars)}
                    </p>
                  </div>
                  <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                    {getFileExtension(doc.source).toUpperCase()}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {uploadError && (
          <p className="text-xs text-destructive leading-snug text-right">{uploadError}</p>
        )}

        {/* Actions */}
        <div className="flex gap-2">
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

          {totalChunks > 0 && me?.is_admin && (
            <Button
              variant="outline"
              className="gap-2 text-destructive hover:text-destructive"
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
