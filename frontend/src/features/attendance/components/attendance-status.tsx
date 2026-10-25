import type { ReactNode } from 'react'
import { Badge, type Tone } from '@/shared/ui/display'
import { attendanceLabels, readableStatus, type AttendanceCode } from '../policy-model'

/** Display order for the legend. */
export const STATUS_CODES = Object.keys(attendanceLabels) as AttendanceCode[]

/** A day result as a soft pill whose colour comes from its code (see attendance-workspace.css). */
export function DayStatus({ status }: { status: string }) {
  return <span className="att-pill" data-st={status}><i aria-hidden="true" />{attendanceLabels[status as AttendanceCode] ?? readableStatus(status)}</span>
}

const REQUEST_TONE: Record<string, Tone> = { approved: 'green', rejected: 'stamp', pending: 'amber', awaiting_cover: 'amber' }

/** A request or approval state. */
export function RequestStatus({ status }: { status: string }) {
  return <Badge variant="dot" tone={REQUEST_TONE[status] ?? 'neutral'}>{readableStatus(status)}</Badge>
}

export function StatusLegend({ codes = STATUS_CODES }: { codes?: AttendanceCode[] }) {
  return <ul className="att-legend" aria-label="What the short codes mean">{codes.map((code) => <li key={code}><span className="att-swatch" data-st={code}>{code}</span>{attendanceLabels[code]}</li>)}</ul>
}

/** An inline message bar (error, saved, warning or plain note). */
export function Callout({ tone = 'neutral', children, role }: { tone?: 'neutral' | 'error' | 'ok' | 'warn'; children: ReactNode; role?: 'alert' | 'status' }) {
  return <p role={role} className={`att-callout att-callout-${tone}`}>{children}</p>
}

/** Card heading: title and description on the left, controls on the right; wraps instead of truncating. */
export function CardHead({ title, meta, action }: { title: string; meta?: string; action?: ReactNode }) {
  return <header className="att-head"><div><h2>{title}</h2>{meta && <p>{meta}</p>}</div>{action && <div className="att-head-actions">{action}</div>}</header>
}
