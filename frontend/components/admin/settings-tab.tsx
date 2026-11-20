"use client"

import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { AdminSettings, getSettings, saveSettings } from "@/lib/admin-api"
import { CARD, ErrorNote } from "./shared"

type ToggleKey = "require_approval" | "keyword_spellings"
type NumericKey = Exclude<keyof AdminSettings, ToggleKey>

const TOGGLES: { key: ToggleKey; label: string; hint: string }[] = [
  { key: "require_approval", label: "משתמשים חדשים דורשים אישור",
    hint: "כשמסומן, מי שנכנס עם גוגל בפעם הראשונה לא יכול לשאול עד שתאשר אותו במסך המשתמשים." },
  { key: "keyword_spellings", label: "חיפוש מילים גם בכתיב החלופי",
    hint: "כשמסומן, חיפוש המילים מחפש גם קרית/קריית, ועדה/וועדה, בניה/בנייה. אפשר לכבות ולהשוות בבנצ׳מרק." },
]

const FIELDS: { key: NumericKey; label: string; hint: string; step: string }[] = [
  { key: "default_daily_limit", label: "מכסת שאלות יומית למשתמש", hint: "0 = ללא הגבלה. אפשר לקבוע מכסה אישית במסך המשתמשים.", step: "1" },
  { key: "monthly_budget_usd", label: "תקרת עלות חודשית ($)", hint: "כשההערכה החודשית מגיעה לתקרה, המערכת מפסיקה לענות למשתמשים (לא לך). 0 = ללא תקרה.", step: "1" },
  { key: "price_input_per_m", label: "מחיר קלט ($ למיליון טוקנים)", hint: "משמש לחישוב הערכת העלות.", step: "0.01" },
  { key: "price_output_per_m", label: "מחיר פלט ($ למיליון טוקנים)", hint: "כולל טוקנים של חשיבה.", step: "0.01" },
  { key: "price_embedding_per_m", label: "מחיר אינדוקס מיידי ($ למיליון טוקנים)", hint: "עד 29 מסמכים בבת אחת: נשלחים מיד.", step: "0.01" },
  { key: "price_embedding_batch_per_m", label: "מחיר אינדוקס מרוכז ($ למיליון טוקנים)", hint: "30 מסמכים ומעלה: נשלחים ל-Batch API של גוגל, בחצי מחיר, ומסתיימים תוך עד 24 שעות.", step: "0.001" },
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
    <div className="max-w-xl space-y-3">
      {TOGGLES.map(t => (
        <label key={t.key} className={`${CARD} flex cursor-pointer items-start gap-3 p-5`}>
          <input
            type="checkbox"
            className="mt-1 h-4 w-4 accent-foreground"
            checked={draft[t.key]}
            onChange={e => { setDraft({ ...draft, [t.key]: e.target.checked }); setNotice("") }}
          />
          <span>
            <span className="block text-sm font-medium">{t.label}</span>
            <span className="mt-0.5 block text-xs leading-relaxed text-muted-foreground">{t.hint}</span>
          </span>
        </label>
      ))}

      {FIELDS.map(f => (
        <label key={f.key} className={`${CARD} block p-5`}>
          <span className="block text-sm font-medium">{f.label}</span>
          <span className="mt-0.5 block text-xs leading-relaxed text-muted-foreground">{f.hint}</span>
          <Input
            type="number"
            min={0}
            step={f.step}
            className="mt-3 w-40"
            value={draft[f.key]}
            onChange={e => { setDraft({ ...draft, [f.key]: Number(e.target.value) || 0 }); setNotice("") }}
          />
        </label>
      ))}

      <ErrorNote message={error} />
      <div className="flex items-center gap-3 pt-2">
        <Button disabled={!dirty} onClick={save}>שמור הגדרות</Button>
        {notice && <span className="text-sm text-emerald-600 dark:text-emerald-400">{notice}</span>}
      </div>
    </div>
  )
}
