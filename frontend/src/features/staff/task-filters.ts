export type TaskView = 'open' | 'due' | 'overdue' | 'escalated' | 'initial' | 'missing' | 'completed' | 'history'

export function taskViewFromParams(params: URLSearchParams, supervisor: boolean, notificationTask?: { completedAt?: string }): TaskView {
  if (supervisor && params.get('date')) return 'history'
  const view = params.get('view')
  if (view === 'escalated') return supervisor ? view : 'open'
  if (view === 'due' || view === 'overdue' || view === 'initial' || view === 'missing' || view === 'completed') return view
  if (!view && params.get('task') && notificationTask?.completedAt) return 'completed'
  return 'open'
}

export function validDateFilter(value: string | null) {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return ''
  const date = new Date(`${value}T00:00:00Z`)
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value ? value : ''
}

/** Completion period is inclusive in the agency timezone, not the browser zone. */
export function completedInPeriod(completedAt: string | undefined, from: string, to: string, timezone = 'Asia/Kolkata') {
  if (!completedAt) return false
  const date = new Date(completedAt)
  if (!Number.isFinite(date.getTime())) return false
  const day = new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(date)
  return (!from || day >= from) && (!to || day <= to)
}
