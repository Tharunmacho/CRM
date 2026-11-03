import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { ArrowRight, ClipboardCheck, X } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { useStore } from '@/shared/state/crm-store'
import { promptMessage, promptState, spanLabel, msUntil } from './daily-work-model'
import { useDailyWork, useTicker } from './use-daily-work'
import '@/styles/daily-work.css'

const HIDE_MS = 30 * 60_000

function readHidden(key: string): { until: number; overdue: number } | null {
  try { return JSON.parse(sessionStorage.getItem(key) || 'null') } catch { return null }
}

/**
 * "Complete your tasks first": a banner at the top of every page (except the Tasks page itself) while the
 * recruiter's daily list for today still has unfinished candidates. It never blocks the app, can be hidden for half
 * an hour, and comes back at once if another hourly deadline is missed.
 */
export function DailyWorkPrompt() {
  const store = useStore()
  const location = useLocation()
  const isStaff = store.me.role === 'staff'
  const { data, skew } = useDailyWork({ enabled: isStaff, debounceMs: 4000 })
  const onTasks = location.pathname === '/tasks' || location.pathname.startsWith('/tasks/')
  const now = useTicker(30_000, isStaff && !onTasks)
  const key = `crm-daily-prompt:${store.me.id}:${data?.agencyDate ?? ''}`
  const [hidden, setHidden] = useState<{ until: number; overdue: number } | null>(null)
  useEffect(() => { setHidden(data ? readHidden(key) : null) }, [key, data])

  if (!isStaff || !data || onTasks) return null
  const state = promptState(data.summary)
  if (state === 'none' || state === 'done') return null
  const summary = data.summary
  if (hidden && now < hidden.until && summary.overdue <= hidden.overdue) return null
  const next = summary.nextDueAt ? msUntil(summary.nextDueAt, now, skew) : null

  const hide = () => {
    const value = { until: Date.now() + HIDE_MS, overdue: summary.overdue }
    try { sessionStorage.setItem(key, JSON.stringify(value)) } catch { /* private mode: hide for this page view only */ }
    setHidden(value)
  }

  return (
    <div className={cn('dw-prompt', (state === 'overdue' || state === 'postponed') && 'is-alert')} role="alert">
      <span className="dw-prompt-icon"><ClipboardCheck size={20} aria-hidden="true" /></span>
      <div className="dw-prompt-text">
        <b>Complete your tasks first</b>
        <span>{promptMessage(summary)}{next !== null && next > 0 && summary.overdue === 0 ? ` Next deadline in ${spanLabel(next)}.` : ''}</span>
      </div>
      <Link to="/tasks" className="dw-prompt-go">Open my tasks<ArrowRight size={15} aria-hidden="true" /></Link>
      <button type="button" className="dw-prompt-x" onClick={hide} aria-label="Hide this reminder for 30 minutes" title="Hide for 30 minutes"><X size={16} aria-hidden="true" /></button>
    </div>
  )
}
