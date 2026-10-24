import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Clock3, LogIn, LogOut } from 'lucide-react'
import { Badge } from '@/shared/ui/display'
import { Button } from '@/shared/ui/primitives'
import { useStore } from '@/shared/state/crm-store'
import { useAttendance } from '../attendance-context'
import { durationLabel, type PresenceStatus } from '../model'
import '../attendance-clock.css'

export function PresenceBadge({ status }: { status: PresenceStatus }) {
  return <Badge dot tone={status === 'online' ? 'green' : status === 'idle' ? 'amber' : 'neutral'}>{status === 'online' ? 'Online in CRM' : status === 'idle' ? 'Idle in CRM' : 'Away or offline'}</Badge>
}

/**
 * The attendance clock. `compact` is the top-bar chip (a link to the attendance page); the full
 * version is the "Your attendance today" card with the check in / check out button and, when the
 * workday length is known, how much of it has been covered.
 */
export function AttendanceClock({ compact = false, targetMinutes }: { compact?: boolean; targetMinutes?: number }) {
  const { me, agencyTimezone } = useStore()
  const { own, loading, error, presenceError, busy, pendingKind, refreshedAt, refresh, clock } = useAttendance()
  const [now, setNow] = useState(performance.now())
  useEffect(() => { const interval = setInterval(() => setNow(performance.now()), 15_000); return () => clearInterval(interval) }, [])
  const checkedIn = own?.attendance.status === 'checked_in'
  // A suspended/background page cannot keep presenting an unverified clock indefinitely.
  const seconds = (own?.attendance.totalSeconds ?? 0) + (checkedIn && refreshedAt && !error ? Math.min(30, Math.max(0, (now - refreshedAt) / 1000)) : 0)
  const action = pendingKind ?? (checkedIn ? 'check-out' : 'check-in')
  const label = (pendingKind && !busy ? 'Retry ' : '') + (action === 'check-out' ? 'Check out' : 'Check in')

  if (me.role === 'admin') return compact ? <Link to="/attendance" className="att-chip" aria-label="Open team attendance"><Clock3 size={16} aria-hidden="true" /><span className="att-chip-text">Attendance</span></Link> : null

  if (compact) return <Link to="/attendance" className="att-chip" data-state={error ? 'error' : checkedIn ? 'in' : 'out'} aria-label="Open attendance">
    <span className="att-chip-icon" aria-hidden="true"><Clock3 size={15} /></span>
    <span className="att-chip-text">{error ? <span>Attendance not available</span> : loading ? <span>Attendance…</span> : checkedIn ? <><strong>{durationLabel(seconds)}</strong> checked in</> : <span>Checked out</span>}</span>
    {me.role === 'staff' && presenceError ? <span className="att-chip-extra att-chip-warn">Update delayed</span> : me.role === 'staff' && own && <span className="att-chip-extra"><PresenceBadge status={own.presence.status} /></span>}
  </Link>

  const percent = targetMinutes && targetMinutes > 0 ? Math.min(100, Math.round((seconds / 60 / targetMinutes) * 100)) : null
  return <section className="att-clock" aria-label="Your attendance today" data-state={checkedIn ? 'in' : 'out'}>
    <div className="att-clock-who">
      <span className="att-clock-icon" aria-hidden="true"><Clock3 size={20} /></span>
      <div>
        <p className="att-clock-label">Your attendance today</p>
        <div className="att-clock-time"><strong>{loading ? 'Loading…' : durationLabel(seconds)}</strong>{own && <Badge dot tone={checkedIn ? 'green' : 'neutral'}>{checkedIn ? 'Checked in' : 'Checked out'}</Badge>}{me.role === 'staff' && own && !presenceError && <PresenceBadge status={own.presence.status} />}</div>
      </div>
    </div>
    {percent !== null && !loading && <div className="att-clock-progress">
      <div className="att-clock-meter" role="meter" aria-label="Share of the workday covered" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><span style={{ width: `${percent}%` }} /></div>
      <small>{percent}% of the {durationLabel((targetMinutes ?? 0) * 60)} workday</small>
    </div>}
    <div className="att-clock-action">
      <Button variant={checkedIn ? 'secondary' : 'primary'} disabled={loading || busy || !own || (!!error && !pendingKind)} icon={action === 'check-out' ? <LogOut size={16} /> : <LogIn size={16} />} onClick={() => void clock()}>{busy ? 'Saving…' : label}</Button>
    </div>
    <div className="att-clock-foot">
      {own?.attendance.checkedInAt && <p>Current session started {new Intl.DateTimeFormat('en-GB', { timeZone: agencyTimezone || 'Asia/Kolkata', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(own.attendance.checkedInAt))} ({agencyTimezone || 'Asia/Kolkata'}).</p>}
      <p>Check out when your shift ends. Closing the browser or signing out does not check you out.</p>
      {loading && <p role="status">Loading your attendance…</p>}
      {error && <div role="alert" className="att-clock-error"><span>{error}</span><Button size="sm" disabled={busy} onClick={() => void refresh()}>Refresh attendance</Button></div>}
      {me.role === 'staff' && presenceError && <p role="status" className="att-clock-warn">CRM use could not be updated just now. {presenceError} Your check-in and check-out are saved anyway.</p>}
    </div>
  </section>
}
