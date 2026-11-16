"use client"

import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { AdminSettings, getSettings, saveSettings } from "@/lib/admin-api"
import { ErrorNote } from "./shared"

type NumericKey = Exclude<keyof AdminSettings, "require_approval">

const FIELDS: { key: NumericKey; label: string; hint: string; step: string }[] = [
  { key: "default_daily_limit", label: "מכסת שאלות יומית למשתמש", hint: "0 = ללא הגבלה. אפשר לקבוע מכסה אישית במסך המשתמשים.", step: "1" },
  { key: "monthly_budget_usd", label: "תקרת עלות חודשית ($)", hint: "כשההערכה החודשית מגיעה לתקרה, המערכת מפסיקה לענות למשתמשים (לא לך). 0 = ללא תקרה.", step: "1" },
  { key: "price_input_per_m", label: "מחיר קלט ($ למיליון טוקנים)", hint: "משמש לחישוב הערכת העלות.", step: "0.01" },
  { key: "price_output_per_m", label: "מחיר פלט ($ למיליון טוקנים)", hint: "כולל טוקנים של חשיבה.", step: "0.01" },
  { key: "price_embedding_per_m", label: "מחיר אינדוקס ($ למיליון טוקנים)", hint: "עלות הפיכת מסמכים לווקטורים.", step: "0.01" },
]

export function SettingsTab({ token }: { token: string | undefined }) {
  const [saved, setSaved] = useState<AdminSettings | null>(null)
  const [draft, setDraft] = useState<AdminSettings | null>(null)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")

  useEffect(() => {
    getSettings(token).then(s => { setSaved(s); setDraft(s) }).catch(e => setError(e.message))
  }, [token])

  if (!draft) return error ? <ErrorNote message={error} /> : <p className="text-sm text-muted-foreground">טוען…</p>

  const dirty = JSON.stringify(draft) !== JSON.stringify(saved)
  const save = async () => {
    setError("")
    try {
      const next = await saveSettings(draft, token)
      setSaved(next)
      setDraft(next)
      setNotice("נשמר")
    } catch (e) {
      setError(e instanceof Error ? e.message : "השמירה נכשלה")
    }
  }

  return (
    <div className="max-w-xl space-y-5">
      <label className="flex items-start gap-3 rounded-xl border bg-card p-4">
        <input
          type="checkbox"
          className="mt-1"
          checked={draft.require_approval}
          onChange={e => { setDraft({ ...draft, require_approval: e.target.checked }); setNotice("") }}
        />
        <span>
          <span className="block text-sm font-medium">משתמשים חדשים דורשים אישור</span>
          <span className="block text-xs text-muted-foreground">
            כשמסומן, מי שנכנס עם גוגל בפעם הראשונה לא יכול לשאול עד שתאשר אותו במסך המשתמשים.
          </span>
        </span>
      </label>

      {FIELDS.map(f => (
        <label key={f.key} className="block rounded-xl border bg-card p-4">
          <span className="block text-sm font-medium">{f.label}</span>
          <span className="block text-xs text-muted-foreground">{f.hint}</span>
          <Input
            type="number"
            min={0}
            step={f.step}
            className="mt-2 h-9 w-40"
            value={draft[f.key]}
            onChange={e => { setDraft({ ...draft, [f.key]: Number(e.target.value) || 0 }); setNotice("") }}
          />
        </label>
      ))}

      <ErrorNote message={error} />
      <div className="flex items-center gap-3">
        <Button disabled={!dirty} onClick={save}>שמור הגדרות</Button>
        {notice && <span className="text-sm text-emerald-600">{notice}</span>}
      </div>
    </div>
  )
}
