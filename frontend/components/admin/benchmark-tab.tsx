"use client"

import { useCallback, useEffect, useState } from "react"
import { Download, Hammer, Play, Square, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { getLibraryAgents, LibraryAgent } from "@/lib/admin-api"
import {
  cancelEvalJob, deleteEvalSet, EvalBudget, EvalJob, EvalQuestion, EvalRun, EvalSet, getEvalOverview,
  importRealQuestions, setEvalBudget,
} from "@/lib/admin-eval-api"
import { cn } from "@/lib/utils"
import { BuildDialog, GoldDialog, RunDialog } from "./benchmark-dialogs"
import { QuestionsTable } from "./benchmark-questions"
import { MetricCards, RunsTable } from "./benchmark-runs"
import { DocumentViewer } from "./document-viewer"
import { selectClass } from "./filter-fields"
import { CARD, ErrorNote, num, Pill, TONE, usdPrecise } from "./shared"

const SET_STATUS: Record<EvalSet["status"], { label: string; className: string } | null> = {
  ready: null,
  building: { label: "בבנייה", className: TONE.amber },
  partial: { label: "חלקי", className: TONE.orange },
  failed: { label: "נכשל", className: TONE.red },
}

/** Retrieval benchmark: question sets with known answers, measurement runs and their comparison. */
export function BenchmarkTab({ token }: { token: string | undefined }) {
  const [agents, setAgents] = useState<LibraryAgent[]>([])
  const [agentId, setAgentId] = useState("")
  const [sets, setSets] = useState<EvalSet[]>([])
  const [runs, setRuns] = useState<EvalRun[]>([])
  const [job, setJob] = useState<EvalJob | null>(null)
  const [budget, setBudget] = useState<EvalBudget | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [setId, setSetId] = useState<string | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [building, setBuilding] = useState(false)
  const [running, setRunning] = useState(false)
  const [labelQ, setLabelQ] = useState<EvalQuestion | null>(null)
  const [newIn, setNewIn] = useState<EvalSet | null>(null)
  const [openDoc, setOpenDoc] = useState<string | null>(null)

  useEffect(() => {
    getLibraryAgents(token)
      .then(d => { setAgents(d.agents); setAgentId(id => id || d.agents[0]?.id || "") })
      .catch(e => setError(e.message))
  }, [token])

  const load = useCallback(async () => {
    if (!agentId) return
    try {
      const d = await getEvalOverview(agentId, token)
      setSets(d.sets)
      setRuns(d.runs)
      setJob(d.job)
      setBudget(d.budget)
      setLoaded(true)
      // keep the chosen set while it exists; otherwise the set of the latest run, or the newest set
      setSetId(id => (id && d.sets.some(s => s.id === id) ? id : d.runs[0]?.set_id ?? d.sets[0]?.id ?? null))
    } catch (e) {
      setError(e instanceof Error ? e.message : "הטעינה נכשלה")
    }
  }, [agentId, token])

  useEffect(() => { load() }, [load])

  // While a job runs, refresh every few seconds; when it ends, reload the questions too.
  const jobRunning = !!job?.running
  useEffect(() => {
    if (!jobRunning) return
    const timer = setInterval(load, 2500)
    return () => {
      clearInterval(timer)
      setReloadKey(k => k + 1)
    }
  }, [jobRunning, load])

  // The questions table shows the latest finished run of the chosen set, unless another run is picked.
  const setRuns_ = runs.filter(r => r.set_id === setId)
  useEffect(() => {
    setRunId(id => (id && setRuns_.some(r => r.id === id && r.status !== "running")
      ? id : setRuns_.find(r => r.status === "done")?.id ?? null))
  }, [setId, runs]) // eslint-disable-line react-hooks/exhaustive-deps

  const set = sets.find(s => s.id === setId) ?? null

  const importReal = async () => {
    setError("")
    setNotice("")
    try {
      const res = await importRealQuestions(agentId, 90, token)
      setNotice(`נוספו ${num(res.added)} שאלות חדשות (מתוך ${num(res.found)} שאלות מ-90 הימים האחרונים) לסט ״${res.set.name}״. `
        + "שאלה נכנסת למדידה אחרי שמסמנים לה את ההחלטה הנכונה (סמל העיפרון).")
      setSetId(res.set.id)
      await load()
      setReloadKey(k => k + 1)
    } catch (e) {
      setError(e instanceof Error ? e.message : "הייבוא נכשל")
    }
  }

  const removeSet = async () => {
    if (!set || !confirm(`למחוק את הסט ״${set.name}״, ${set.questions} השאלות שבו וכל הריצות שלו? אי אפשר לבטל.`)) return
    try {
      await deleteEvalSet(set.id, token)
      setSetId(null)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : "המחיקה נכשלה")
    }
  }

  if (!loaded && !error) return <p className="text-sm text-muted-foreground">טוען…</p>

  return (
    <div className="space-y-4">
      <div className={cn(CARD, "space-y-3 p-4")}>
        <p className="text-sm leading-relaxed text-muted-foreground">
          סט של שאלות שידוע עליהן איזו החלטה עונה עליהן. כל ״מדידה״ שולחת את השאלות לחיפוש ובודקת באיזה מקום הופיעה ההחלטה הנכונה —
          כך רואים אם שינוי בחיפוש, בחיתוך הקטעים או בפרסור שיפר או הרע, לפני שהוא מגיע למשתמשים.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          {agents.length > 1 && (
            <select className={selectClass} value={agentId} onChange={e => { setAgentId(e.target.value); setSetId(null) }}>
              {agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          )}
          <select className={cn(selectClass, "max-w-[20rem]")} value={setId ?? ""} onChange={e => setSetId(e.target.value || null)}
            disabled={!sets.length} aria-label="סט שאלות">
            {!sets.length && <option value="">אין עדיין סטים</option>}
            {sets.map(s => (
              <option key={s.id} value={s.id}>{s.name} · {num(s.questions)} שאלות, {num(s.labelled)} מסומנות</option>
            ))}
          </select>
          {set && SET_STATUS[set.status] && <Pill className={SET_STATUS[set.status]!.className}>{SET_STATUS[set.status]!.label}</Pill>}
          {set?.info.cost_usd != null && <span className="text-xs text-muted-foreground">עלות הבנייה {usdPrecise(set.info.cost_usd)}</span>}
          {set && (
            <Button size="icon" variant="ghost" className="h-8 w-8" title="מחק סט" onClick={removeSet} disabled={jobRunning}>
              <Trash2 className="h-4 w-4" />
            </Button>
          )}
          <span className="flex-1" />
          <Button size="sm" variant="outline" className="gap-1.5" disabled={jobRunning} onClick={() => setBuilding(true)}>
            <Hammer className="h-4 w-4" /> בנה סט שאלות
          </Button>
          <Button size="sm" variant="outline" className="gap-1.5" onClick={importReal}>
            <Download className="h-4 w-4" /> ייבא שאלות אמיתיות
          </Button>
          <Button size="sm" className="gap-1.5" disabled={jobRunning || !set?.labelled} onClick={() => setRunning(true)}
            title={set && !set.labelled ? "אין בסט שאלות עם החלטה נכונה מסומנת" : undefined}>
            <Play className="h-4 w-4" /> הרץ מדידה
          </Button>
        </div>
        {set?.info.error && <ErrorNote message={`בניית הסט נעצרה: ${set.info.error}`} />}
        {budget && <BudgetBar budget={budget} token={token} onSaved={setBudget} />}
      </div>

      <JobBanner job={job} token={token} onCancelled={load} />
      <ErrorNote message={error} />
      {notice && <p className="rounded-xl border bg-card px-4 py-2.5 text-sm">{notice}</p>}

      {set && <MetricCards runs={setRuns_} />}
      <RunsTable runs={runs} selected={runId} onSelect={id => {
        const r = runs.find(x => x.id === id)
        if (r) { setSetId(r.set_id); setRunId(id) }
      }} token={token} />

      {set ? (
        <QuestionsTable token={token} set={set} runs={runs} runId={runId} onRunChange={setRunId} reloadKey={reloadKey}
          onLabel={setLabelQ} onAdd={() => setNewIn(set)} onOpenDoc={setOpenDoc} />
      ) : (
        <div className={cn(CARD, "p-8 text-center text-sm text-muted-foreground")}>
          אין עדיין סט שאלות. ״בנה סט שאלות״ יוצר סט מהאינדקס (מציג עלות לפני שמתחיל); ״ייבא שאלות אמיתיות״ מעתיק שאלות מהצ׳אט לסימון ידני.
        </div>
      )}

      <BuildDialog open={building} agentId={agentId} budget={budget} token={token} onClose={() => setBuilding(false)}
        onStarted={s => { setBuilding(false); setSetId(s.id); load() }} />
      <RunDialog open={running} agentId={agentId} set={set} budget={budget} token={token} onClose={() => setRunning(false)}
        onStarted={() => { setRunning(false); load() }} />
      <GoldDialog question={labelQ} newIn={newIn} agentId={agentId} token={token}
        onClose={() => { setLabelQ(null); setNewIn(null) }}
        onSaved={() => { setLabelQ(null); setNewIn(null); setReloadKey(k => k + 1); load() }} />
      {openDoc && agentId && (
        <DocumentViewer agentId={agentId} recordKey={openDoc} token={token} initialView="chunks" onClose={() => setOpenDoc(null)} />
      )}
    </div>
  )
}

function JobBanner({ job, token, onCancelled }: { job: EvalJob | null; token: string | undefined; onCancelled: () => void }) {
  if (!job) return null
  const what = job.kind === "build" ? "בונה סט שאלות" : "מריץ מדידה"
  if (!job.running) {
    if (job.error) return <ErrorNote message={`העבודה האחרונה (${what}${job.label ? ` ״${job.label}״` : ""}) נעצרה: ${job.error}`} />
    return null
  }
  const share = job.total ? Math.min(1, job.done / job.total) : 0
  return (
    <div className={cn(CARD, "space-y-2 p-4")}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="h-2 w-2 animate-pulse rounded-full bg-amber-500" />
        <span className="font-medium">{what}{job.label ? ` ״${job.label}״` : ""}</span>
        <span className="text-muted-foreground">{job.message}</span>
        <span className="flex-1" />
        <span className="tabular-nums text-xs text-muted-foreground">
          {num(job.done)} / {num(job.total)}{` · הוצא עד עכשיו ${usdPrecise(job.spent_usd)}`}
        </span>
        <Button size="sm" variant="ghost" className="h-7 gap-1 text-xs"
          onClick={() => cancelEvalJob(token).then(onCancelled).catch(() => {})}>
          <Square className="h-3 w-3" /> עצור
        </Button>
      </div>
      <div className="h-2 rounded-full bg-muted">
        <div className="h-2 rounded-full bg-foreground/60 transition-all" style={{ width: `${share * 100}%` }} />
      </div>
    </div>
  )
}

/** Spent so far against the benchmark budget; paid actions above what is left are refused. */
function BudgetBar({ budget, token, onSaved }: {
  budget: EvalBudget
  token: string | undefined
  onSaved: (b: EvalBudget) => void
}) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(String(budget.budget_usd))
  const [error, setError] = useState("")
  const share = budget.budget_usd > 0 ? Math.min(1, budget.spent_usd / budget.budget_usd) : 1

  const save = async () => {
    const n = Number(value)
    if (!Number.isFinite(n) || n < 0) return setError("סכום לא תקין")
    try {
      onSaved(await setEvalBudget(n, token))
      setEditing(false)
      setError("")
    } catch (e) {
      setError(e instanceof Error ? e.message : "השמירה נכשלה")
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 border-t pt-3 text-xs">
      <span className="text-muted-foreground">תקציב הבנצ׳מרק:</span>
      <span className="font-medium tabular-nums">הוצא {usdPrecise(budget.spent_usd)} מתוך {usdPrecise(budget.budget_usd)}</span>
      <div className="h-1.5 w-28 rounded-full bg-muted">
        <div className={cn("h-1.5 rounded-full", share >= 0.9 ? "bg-red-500" : "bg-foreground/50")} style={{ width: `${share * 100}%` }} />
      </div>
      <span className="text-muted-foreground">כל פעולה בתשלום מציגה הערכה לפני שהיא מתחילה, ונחסמת אם היא חורגת ממה שנשאר.</span>
      <span className="flex-1" />
      {editing ? (
        <>
          <span dir="ltr" className="flex items-center gap-1">$
            <Input type="number" min={0} step="0.5" className="h-7 w-20 text-xs" value={value} onChange={e => setValue(e.target.value)} />
          </span>
          <Button size="sm" className="h-7 text-xs" onClick={save}>שמור</Button>
          <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => setEditing(false)}>ביטול</Button>
        </>
      ) : (
        <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => { setValue(String(budget.budget_usd)); setEditing(true) }}>
          שנה תקציב
        </Button>
      )}
      {error && <span className="text-red-600 dark:text-red-400">{error}</span>}
    </div>
  )
}