"use client"

import { useEffect, useState } from "react"
import { Plus, Search, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  addEvalQuestion, buildEvalSet, BuildEstimate, BuildMode, Decision, EvalBudget, EvalQuestion, EvalSet,
  getGoldSuggestions, lookupDecisions, patchEvalQuestion, RunEstimate, RunMode, startEvalRun,
} from "@/lib/admin-eval-api"
import { cn } from "@/lib/utils"
import { MODE_LABEL, TYPE_LABEL } from "./benchmark-labels"
import { selectClass } from "./filter-fields"
import { ErrorNote, num, usdPrecise } from "./shared"

const errorText = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback)

const overBudget = (budget: EvalBudget | null, cost: number) => !!budget && cost > budget.remaining_usd

const BUILD_MODES: Record<BuildMode, { label: string; hint: string }> = {
  decisions: {
    label: "בסגנון שמאים (מומלץ)",
    hint: "דוגמים החלטות שלמות. חלק מהשאלות נבנות מהכותרת ומגוש/חלקה בלי מודל; את השאר כותב מודל זול: פרט מהחלטה " +
      "(עם התשובה וציטוט מדויק מההחלטה — ציטוט שלא נמצא בהחלטה נזרק), ניתוח החלטה (עם הנקודות שתשובה טובה צריכה), " +
      "ושאלות נושא על כל המאגר (כופר חניה, פטור 140 מ״ר וכו׳) שההחלטות הנכונות שלהן הן כל ההחלטות שדנות בנושא.",
  },
  chunks: {
    label: "שאלה לכל קטע",
    hint: "דוגמים החלטות מהאינדקס. כ-40% מהשאלות נבנות מפרטי ההחלטה (גוש/חלקה, שמאי, ועדה) בלי מודל; את השאר כותב " +
      "מודל זול — שאלה אחת לקטע, ושאלה שמעתיקה מהקטע נזרקת. ההחלטה שממנה נכתבה השאלה היא ״ההחלטה הנכונה״.",
  },
}

/** What is left of the benchmark budget, and whether this action fits in it. */
function BudgetLine({ budget, cost }: { budget: EvalBudget | null; cost: number }) {
  if (!budget) return null
  return (
    <p className={cn("mt-1 text-xs", overBudget(budget, cost) ? "text-red-600 dark:text-red-400" : "text-muted-foreground")}>
      נשאר בתקציב הבנצ׳מרק {usdPrecise(budget.remaining_usd)} מתוך {usdPrecise(budget.budget_usd)}
      {overBudget(budget, cost) && " — הפעולה חורגת מהתקציב. אפשר להגדיל אותו בראש הלשונית."}
    </p>
  )
}

function Shell({ open, title, description, onClose, children }: {
  open: boolean
  title: string
  description?: React.ReactNode
  onClose: () => void
  children: React.ReactNode
}) {
  return (
    <Dialog open={open} onOpenChange={o => !o && onClose()}>
      <DialogContent dir="rtl" className="max-h-[92vh] max-w-xl overflow-y-auto text-right">
        <DialogHeader className="space-y-1.5 pe-8 text-right sm:text-right">
          <DialogTitle className="text-lg font-semibold">{title}</DialogTitle>
          {description && <DialogDescription className="text-xs leading-relaxed">{description}</DialogDescription>}
        </DialogHeader>
        <div className="space-y-3 text-sm">{children}</div>
      </DialogContent>
    </Dialog>
  )
}

/** Build a synthetic set: first the estimate (free), then a confirmed, paid build in the background. */
export function BuildDialog({ open, agentId, budget, token, onClose, onStarted }: {
  open: boolean
  agentId: string
  budget: EvalBudget | null
  token: string | undefined
  onClose: () => void
  onStarted: (set: EvalSet) => void
}) {
  const [n, setN] = useState(60)
  const [name, setName] = useState("")
  const [mode, setMode] = useState<BuildMode>("decisions")
  const [estimate, setEstimate] = useState<BuildEstimate | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => { if (open) { setEstimate(null); setError("") } }, [open])
  useEffect(() => { setEstimate(null) }, [n, mode])

  const go = async (confirm: boolean) => {
    setBusy(true)
    setError("")
    try {
      const res = await buildEvalSet({ agent_id: agentId, n, name, mode, confirm }, token)
      setEstimate(res.estimate)
      if (confirm && res.set) onStarted(res.set)
    } catch (e) {
      setError(errorText(e, "הפעולה נכשלה"))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Shell open={open} onClose={onClose} title="בניית סט שאלות"
      description={BUILD_MODES[mode].hint}>
      <div className="flex flex-wrap gap-1.5">
        {(Object.keys(BUILD_MODES) as BuildMode[]).map(m => (
          <button key={m} className="chip" data-active={mode === m} onClick={() => setMode(m)}>{BUILD_MODES[m].label}</button>
        ))}
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-muted-foreground">
          מספר שאלות
          <Input type="number" min={20} max={150} className="mt-1 h-9 w-28" value={n}
            onChange={e => setN(Math.max(20, Math.min(150, Number(e.target.value) || 20)))} />
        </label>
        <label className="grow text-xs text-muted-foreground">
          שם הסט (לא חובה)
          <Input className="mt-1 h-9" value={name} maxLength={80} onChange={e => setName(e.target.value)} />
        </label>
      </div>
      {estimate && (
        <div className="rounded-xl bg-muted/50 p-3 text-xs leading-relaxed">
          נדגמו {num(estimate.sampled)} החלטות: {num(estimate.meta_questions)} שאלות מפרטי ההחלטה ומהכותרת (חינם)
          ו-{num(estimate.llm_questions)} שיכתוב {estimate.model} — כ-{num(estimate.input_tokens)} טוקנים נכנסים
          ו-{num(estimate.output_tokens)} יוצאים.
          <p className="mt-1 text-base font-semibold text-foreground">עלות משוערת: {usdPrecise(estimate.cost_usd)}</p>
          <BudgetLine budget={budget} cost={estimate.cost_usd} />
        </div>
      )}
      <ErrorNote message={error} />
      <div className="flex gap-2">
        {!estimate ? (
          <Button disabled={busy} onClick={() => go(false)}>{busy ? "מחשב…" : "חשב עלות"}</Button>
        ) : (
          <Button disabled={busy || overBudget(budget, estimate.cost_usd)} onClick={() => go(true)}>
            {busy ? "מתחיל…" : `אשר ובנה (${usdPrecise(estimate.cost_usd)})`}
          </Button>
        )}
        <Button variant="ghost" onClick={onClose}>ביטול</Button>
      </div>
    </Shell>
  )
}

/** Start a measurement run on a set. Pipeline runs show their estimated cost and need a second click. */
export function RunDialog({ open, agentId, set, budget, token, onClose, onStarted }: {
  open: boolean
  agentId: string
  set: EvalSet | null
  budget: EvalBudget | null
  token: string | undefined
  onClose: () => void
  onStarted: () => void
}) {
  const [mode, setMode] = useState<RunMode>("hybrid")
  const [label, setLabel] = useState("")
  const [estimate, setEstimate] = useState<RunEstimate | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => { if (open) { setEstimate(null); setError(""); setMode("hybrid") } }, [open])
  useEffect(() => { setEstimate(null) }, [mode])

  // first click: the estimate (spends nothing); second click: start
  const go = async () => {
    if (!set) return
    setBusy(true)
    setError("")
    try {
      const res = await startEvalRun({ agent_id: agentId, set_id: set.id, mode, label, confirm: !!estimate }, token)
      setEstimate(res.estimate)
      if (res.run) onStarted()
    } catch (e) {
      setError(errorText(e, "הריצה לא התחילה"))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Shell open={open} onClose={onClose} title="הרצת מדידה"
      description={<>כל שאלה בסט ״{set?.name}״ שיש לה החלטה נכונה מסומנת ({num(set?.labelled)}) נשלחת לחיפוש, ובודקים באיזה מקום הופיעה ההחלטה הנכונה. שום דבר לא נרשם ביומן השאלות.</>}>
      <div className="space-y-2">
        {(Object.keys(MODE_LABEL) as RunMode[]).map(m => (
          <label key={m} className={cn("flex cursor-pointer items-start gap-2 rounded-xl border p-3",
            mode === m && "border-foreground/40 bg-muted/40")}>
            <input type="radio" className="mt-1" checked={mode === m} onChange={() => setMode(m)} />
            <span>
              <span className="font-medium">{MODE_LABEL[m].label}</span>
              {m === "pipeline" && <span className="ms-2 rounded-full bg-amber-50 px-2 py-0.5 text-xs text-amber-700 dark:bg-amber-400/10 dark:text-amber-300">בתשלום</span>}
              {m === "hybrid" && <span className="ms-2 text-xs text-muted-foreground">(ברירת מחדל)</span>}
              <span className="block text-xs text-muted-foreground">{MODE_LABEL[m].hint}</span>
            </span>
          </label>
        ))}
      </div>
      <label className="block text-xs text-muted-foreground">
        תווית (מה השתנה? לדוגמה: ״בסיס״, ״חיתוך קטעים חדש״)
        <Input className="mt-1 h-9" value={label} maxLength={60} onChange={e => setLabel(e.target.value)} />
      </label>
      {estimate && (
        <div className="rounded-xl bg-muted/50 p-3 text-xs">
          {num(estimate.questions)} שאלות{mode === "pipeline" ? " דרך הצנרת המלאה" : ", רק יצירת וקטור לכל שאלה"}.
          <span className="mt-1 block text-base font-semibold text-foreground">עלות משוערת: {usdPrecise(estimate.cost_usd)}</span>
          <BudgetLine budget={budget} cost={estimate.cost_usd} />
        </div>
      )}
      <ErrorNote message={error} />
      <div className="flex gap-2">
        <Button disabled={busy || !set || (!!estimate && overBudget(budget, estimate.cost_usd))} onClick={go}>
          {busy ? "רגע…" : !estimate ? "חשב עלות" : `אשר והרץ (${usdPrecise(estimate.cost_usd)})`}
        </Button>
        <Button variant="ghost" onClick={onClose}>ביטול</Button>
      </div>
    </Shell>
  )
}

const TYPES = ["principle", "table", "block_plot", "real", "manual"]

function DecisionRow({ d, action }: { d: Decision; action: React.ReactNode }) {
  return (
    <li className="flex items-center gap-2 rounded-lg bg-muted/40 px-2.5 py-1.5 text-xs">
      <span className="min-w-0 flex-1 truncate">{d.title ?? d.record_key}</span>
      {action}
    </li>
  )
}

/** Mark the gold decision(s) of a question — or, with `newIn`, write a new manual question. */
export function GoldDialog({ question, newIn, agentId, token, onClose, onSaved }: {
  question: EvalQuestion | null
  newIn: EvalSet | null
  agentId: string
  token: string | undefined
  onClose: () => void
  onSaved: () => void
}) {
  const open = !!question || !!newIn
  const [text, setText] = useState("")
  const [type, setType] = useState("manual")
  const [gold, setGold] = useState<Decision[]>([])
  const [hints, setHints] = useState<Decision[]>([])
  const [query, setQuery] = useState("")
  const [found, setFound] = useState<Decision[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => {
    setGold(question?.gold ?? [])
    setType(question?.type ?? "manual")
    setText("")
    setHints([])
    setQuery("")
    setFound(null)
    setError("")
    if (question?.log_id) getGoldSuggestions(question.id, token).then(d => setHints(d.decisions)).catch(() => {})
  }, [question, newIn, token])

  const add = (d: Decision) => setGold(g => (g.some(x => x.record_key === d.record_key) ? g : [...g, d]))
  const search = async () => {
    if (!query.trim()) return
    try {
      setFound((await lookupDecisions(agentId, query.trim(), token)).decisions)
    } catch (e) {
      setError(errorText(e, "החיפוש נכשל"))
    }
  }
  const save = async () => {
    setBusy(true)
    setError("")
    try {
      const keys = gold.map(g => g.record_key)
      if (question) await patchEvalQuestion(question.id, { gold: keys, type }, token)
      else if (newIn) await addEvalQuestion(newIn.id, { question: text, type, gold: keys }, token)
      onSaved()
    } catch (e) {
      setError(errorText(e, "השמירה נכשלה"))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Shell open={open} onClose={onClose} title={question ? "ההחלטה הנכונה לשאלה" : "שאלה ידנית חדשה"}
      description={question ? <>״{question.question}״</> : "שאלה שתרצה למדוד, וההחלטה (או ההחלטות) שעונות עליה."}>
      {!question && (
        <Input className="h-9" placeholder="השאלה, כפי שמשתמש היה כותב אותה" value={text} onChange={e => setText(e.target.value)} />
      )}
      <label className="flex items-center gap-2 text-xs text-muted-foreground">
        סוג שאלה
        <select className={cn(selectClass, "h-8 text-xs")} value={type} onChange={e => setType(e.target.value)}>
          {(TYPES.includes(type) ? TYPES : [...TYPES, type]).map(t => <option key={t} value={t}>{TYPE_LABEL[t] ?? t}</option>)}
        </select>
      </label>
      <div>
        <p className="mb-1 text-xs font-medium">החלטות נכונות</p>
        <ul className="space-y-1">
          {gold.map(d => (
            <DecisionRow key={d.record_key} d={d} action={
              <button aria-label="הסר" onClick={() => setGold(g => g.filter(x => x.record_key !== d.record_key))}>
                <X className="h-3.5 w-3.5" />
              </button>} />
          ))}
          {gold.length === 0 && <li className="text-xs text-muted-foreground">לא סומנה החלטה — השאלה לא תיכלל במדידות.</li>}
        </ul>
      </div>
      {hints.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium">ההחלטות שהתשובה בצ׳אט נשענה עליהן</p>
          <ul className="space-y-1">
            {hints.map(d => (
              <DecisionRow key={d.record_key} d={d} action={
                <Button size="sm" variant="ghost" className="h-6 gap-1 px-2 text-xs" onClick={() => add(d)}><Plus className="h-3 w-3" /> סמן</Button>} />
            ))}
          </ul>
        </div>
      )}
      <div>
        <p className="mb-1 text-xs font-medium">חיפוש החלטה בספרייה</p>
        <div className="flex gap-2">
          <Input className="h-8 text-xs" placeholder="מילה מהכותרת, מספר גוש או מזהה" value={query}
            onChange={e => setQuery(e.target.value)} onKeyDown={e => e.key === "Enter" && search()} />
          <Button size="sm" variant="outline" className="h-8 gap-1" onClick={search}><Search className="h-3.5 w-3.5" /> חפש</Button>
        </div>
        {found && (
          <ul className="mt-2 space-y-1">
            {found.map(d => (
              <DecisionRow key={d.record_key} d={d} action={
                <Button size="sm" variant="ghost" className="h-6 gap-1 px-2 text-xs" onClick={() => add(d)}><Plus className="h-3 w-3" /> סמן</Button>} />
            ))}
            {found.length === 0 && <li className="text-xs text-muted-foreground">לא נמצאו החלטות.</li>}
          </ul>
        )}
      </div>
      <ErrorNote message={error} />
      <div className="flex gap-2">
        <Button disabled={busy || (!question && text.trim().length < 3)} onClick={save}>{busy ? "שומר…" : "שמור"}</Button>
        <Button variant="ghost" onClick={onClose}>ביטול</Button>
      </div>
    </Shell>
  )
}
