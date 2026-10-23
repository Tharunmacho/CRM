export type PresenceStatus = 'online' | 'idle' | 'offline'
export interface AttendanceSession {
  id: string
  checkedInAt: string
  checkedOutAt: string | null
  durationSeconds: number
  correctionReason?: string | null
  correctedBy?: string | null
}
export interface PresenceSummary {
  status: PresenceStatus
  tracked: boolean
  lastSeenAt: string | null
  activeSeconds: number
  idleSeconds: number
  offlineSeconds: number
  sessions: { startedAt: string; endedAt: string | null; status: PresenceStatus; durationSeconds: number }[]
}
export interface StaffAttendance {
  userId: string
  name: string
  role: 'admin' | 'manager' | 'staff'
  active: boolean
  attendance: {
    status: 'checked_in' | 'checked_out'
    sessionId?: string | null
    checkedInAt: string | null
    lastCheckedOutAt: string | null
    totalSeconds: number
    sessions: AttendanceSession[]
  }
  presence: PresenceSummary
}
export interface AttendanceResponse {
  date: string
  timezone: string
  canViewTeam: boolean
  generatedAt: string
  clockContext: string | null
  staff: StaffAttendance[]
}

export const PRESENCE_IDLE_MS = 5 * 60_000
export function inferredPresence(visible: boolean, lastInteractionAt: number, now: number): PresenceStatus {
  if (!visible) return 'offline'
  return now - lastInteractionAt < PRESENCE_IDLE_MS ? 'online' : 'idle'
}
export function durationLabel(seconds: number) {
  const minutes = Math.floor(Math.max(0, Number.isFinite(seconds) ? seconds : 0) / 60)
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`
}
export function activeAttendanceSession(row: StaffAttendance | null) {
  if (!row || row.attendance.status !== 'checked_in') return null
  return row.attendance.sessionId ?? row.attendance.sessions.find((session) => !session.checkedOutAt)?.id ?? null
}
