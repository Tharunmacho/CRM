import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Clock3, LogIn, LogOut } from 'lucide-react'
import { Badge } from '@/shared/ui/display'
import { Button } from '@/shared/ui/primitives'
import { useStore } from '@/shared/state/crm-store'
import { useAttendance } from '../attendance-context'
import { durationLabel, type PresenceStatus } from '../model'

export function PresenceBadge({ status }: { status: PresenceStatus }) {
  return <Badge dot tone={status === 'online' ? 'navy' : status === 'idle' ? 'amber' : 'neutral'}>{status === 'online' ? 'Online' : status === 'idle' ? 'Idle' : 'Offline'}</Badge>
}

export function AttendanceClock({ compact = false }: { compact?: boolean }) {
  const { me } = useStore()
  const { own, loading, error, presenceError, busy, pendingKind, refreshedAt, refresh, clock } = useAttendance()
  const [now, setNow] = useState(performance.now())
  useEffect(() => { const interval = setInterval(() => setNow(performance.now()), 15_000); return () => clearInterval(interval) }, [])
  const checkedIn = own?.attendance.status === 'checked_in'
  const seconds = (own?.attendance.totalSeconds ?? 0) + (checkedIn && refreshedAt && !error ? Math.max(0, (now - refreshedAt) / 1000) : 0)
  const action = pendingKind ?? (checkedIn ? 'check-out' : 'check-in')
  const label = (pendingKind && !busy ? 'Retry ' : '') + (action === 'check-out' ? 'Check out' : 'Check in')

  if (me.role === 'admin') return compact ? <Link to="/attendance" className="flex items-center gap-2 rounded-control px-2 py-2 text-xs text-ink-600 hover:bg-field" aria-label="Open team attendance"><Clock3 className="h-4 w-4" /><span className="hidden md:block">Attendance</span></Link> : null

  if (compact) return <Link to="/attendance" className="flex min-w-0 items-center gap-2 rounded-control px-2 py-2 text-xs text-ink-600 hover:bg-field" aria-label="Open attendance and presence">
    <Clock3 className="h-4 w-4 shrink-0" />
    <span className="hidden min-w-0 sm:block">{error ? 'Attendance unavailable' : loading ? 'Attendance…' : checkedIn ? durationLabel(seconds) + ' checked in' : 'Checked out'}</span>
    {me.role === 'staff' && presenceError ? <span className="hidden text-amber-700 md:block">Sync delayed</span> : me.role === 'staff' && own && <span className="hidden md:block"><PresenceBadge status={own.presence.status} /></span>}
  </Link>

  return <div className="rounded-panel border border-navy-100 bg-navy-50/40 p-5">
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div>
        <p className="text-xs font-medium text-ink-500">Your attendance today</p>
        <div className="mt-2 flex flex-wrap items-center gap-3"><strong className="text-2xl font-semibold text-ink tnum">{durationLabel(seconds)}</strong><Badge tone={checkedIn ? 'navy' : 'neutral'}>{checkedIn ? 'Checked in' : 'Checked out'}</Badge>{me.role === 'staff' && own && <PresenceBadge status={own.presence.status} />}</div>
        <p className="mt-2 text-xs text-ink-500">Check out when your shift ends. Closing the browser or signing out keeps your attendance session open.</p>
      </div>
      <Button variant={checkedIn ? 'secondary' : 'primary'} disabled={loading || busy || !own || (!!error && !pendingKind)} icon={action === 'check-out' ? <LogOut size={16} /> : <LogIn size={16} />} onClick={() => void clock()}>{busy ? 'Saving…' : label}</Button>
    </div>
    {loading && <p className="mt-3 text-xs text-ink-500" role="status">Loading your attendance…</p>}
    {error && <div role="alert" className="mt-3 flex flex-wrap items-center gap-2 text-sm text-stamp-700"><span>{error}</span>{!pendingKind && <Button size="sm" onClick={() => void refresh()}>Retry attendance</Button>}</div>}
    {me.role === 'staff' && presenceError && <p role="status" className="mt-3 text-xs text-amber-700">Presence sync is delayed. {presenceError} Your check-in and check-out are saved separately.</p>}
  </div>
}
