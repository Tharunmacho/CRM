import { useState } from 'react'
import { AttendanceWorkspace } from '@/features/attendance/components/attendance-workspace'
import { PresenceView } from '@/features/attendance/components/attendance-presence'

export default function AttendancePage() {
  const [recordRevision, setRecordRevision] = useState(0)
  return <AttendanceWorkspace recordRevision={recordRevision} presence={(staffId, date) => <PresenceView initialStaffId={staffId} initialDate={date} onUpdated={() => setRecordRevision((value) => value + 1)} />} />
}
