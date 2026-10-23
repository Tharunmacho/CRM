export type StaffAvailabilityStatus = 'available' | 'approved_leave' | 'manual_leave' | 'paused' | 'inactive' | 'not_assigned'

export interface DirectoryEmployee {
  id: string
  name: string
  email: string
  phone?: string | null
  role: 'admin' | 'manager' | 'staff'
  active: boolean
  availability: { status: StaffAvailabilityStatus; label: string; eligible: boolean; leaveType?: string | null }
  assignedCandidates: number
  initialCallsPending: number
  openTasks: number
  overdueTasks: number
  escalatedTasks: number
  missingUpdates: number
  completedTasks: number
  averageCompletionHours: number | null
}

export interface StaffDirectoryResponse {
  generatedAt: string
  today: string
  timezone: string
  period: { from: string; to: string }
  staff: DirectoryEmployee[]
}

export const availabilityOptions: { value: StaffAvailabilityStatus; label: string }[] = [
  { value: 'available', label: 'Available for intake' },
  { value: 'approved_leave', label: 'Approved leave' },
  { value: 'manual_leave', label: 'Marked on leave' },
  { value: 'paused', label: 'Automatic intake paused' },
  { value: 'inactive', label: 'Inactive account' },
  { value: 'not_assigned', label: 'Outside recruitment rotation' },
]

export function staffRoleLabel(role: DirectoryEmployee['role']) {
  return role === 'admin' ? 'Administrator' : role === 'manager' ? 'Manager' : 'Staff'
}

export function taskLink(id: string, view: 'open' | 'initial' | 'overdue' | 'escalated' | 'missing' | 'completed', period?: { from: string; to: string }) {
  const query = new URLSearchParams({ staff: id, view })
  if (view === 'completed' && period) { query.set('from', period.from); query.set('to', period.to) }
  return `/tasks?${query}`
}

export function candidateLink(id: string) { return `/data-bank?${new URLSearchParams({ staff: id })}` }

export function reportLink(id: string, period: { from: string; to: string }) {
  return `/reports?${new URLSearchParams({ staff: id, from: period.from, to: period.to })}`
}

export function validDate(value: string | null): value is string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const date = new Date(`${value}T12:00:00Z`)
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value
}

export function periodLabel(period: { from: string; to: string }) {
  const format = (date: string) => new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${date}T12:00:00Z`))
  return `${format(period.from)} – ${format(period.to)}`
}

export function completionLabel(hours: number | null) {
  if (hours == null) return '—'
  return `${new Intl.NumberFormat('en-GB', { maximumFractionDigits: 1 }).format(hours)} ${hours === 1 ? 'hour' : 'hours'}`
}
