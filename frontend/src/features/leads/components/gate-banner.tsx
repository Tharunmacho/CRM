import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, CheckCircle2, Lock, X } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { useTicker } from '@/features/daily-work/use-daily-work'
import { GATE_REASON, deadlineCountdown, gateBody, gatePostponedLabel, gateProgress, type GateState, type LeadGate } from '../lead-gate'

const UNLOCK_KEY = 'crm-lead-unlock-note'
const readDismissed = (day: string) => { try { return sessionStorage.getItem(UNLOCK_KEY) === day } catch { return false } }

/**
 * Staff who still have daily tasks to finish see this at the top of the Lead sheet: what is left, how far along they are,
 * the next deadline, and one button to the tasks. When the last task is done it turns into a small green note (dismissible).
 * Admin and manager never see it.
 */
export function GateBanner({ state, gate }: { state: GateState; gate: LeadGate | null }) {
  const day = new Date().toDateString()
  const [dismissed, setDismissed] = useState(() => readDismissed(day))
  const now = useTicker(30_000, state === 'locked')
  if (state === 'exempt' || state === 'unknown' || !gate) return null

  if (state === 'open') {
    if (dismissed || gate.total === 0) return null
    return (
      <div className="ld-gate is-open" role="status">
        <CheckCircle2 size={18} aria-hidden="true" />
        <span>Daily tasks complete — lead sheet unlocked.</span>
        <button type="button" className="ld-gate-x" aria-label="Dismiss" onClick={() => { setDismissed(true); try { sessionStorage.setItem(UNLOCK_KEY, day) } catch { /* ignore */ } }}><X size={15} aria-hidden="true" /></button>
      </div>
    )
  }

  const progress = gateProgress(gate)
  const countdown = deadlineCountdown(gate.nextDueAt, now)
  const carried = gatePostponedLabel(gate.postponed)
  return (
    <section className="ld-gate is-locked" role="alert" aria-label={GATE_REASON}>
      <span className="ld-gate-icon"><Lock size={20} aria-hidden="true" /></span>
      <div className="ld-gate-main">
        <h2>Finish today's tasks first</h2>
        <p>{gateBody(gate)}</p>
        <div className="ld-gate-meta">
          <span className="ld-gate-progress" aria-label={progress.label}>
            <i style={{ width: `${progress.percent}%` }} />
          </span>
          <b className="tnum">{progress.label}</b>
          {carried && <span className="ld-gate-chip is-alert">{carried}</span>}
          {countdown && <span className={cn('ld-gate-chip', countdown.late && 'is-alert')}>{countdown.label}</span>}
        </div>
      </div>
      <Link to="/tasks" className="ld-gate-go">Go to my daily tasks<ArrowRight size={15} aria-hidden="true" /></Link>
    </section>
  )
}
