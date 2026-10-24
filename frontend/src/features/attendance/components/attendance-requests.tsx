import type { ReactNode } from 'react'
import { useState } from 'react'
import { Check, CircleDashed, Users, X } from 'lucide-react'
import { EmptyState, Panel } from '@/shared/ui/display'
import { Button } from '@/shared/ui/primitives'
import { Tabs } from '@/shared/ui/overlay'
import { RecordIdentity } from '@/shared/ui/workspace'
import { approvalLabel, readableStatus, requestLabels, type AttendanceRequest } from '../policy-model'
import { dayLabel, stamp } from '../format'
import { CardHead, RequestStatus } from './attendance-status'

export type DecisionAction = 'approve' | 'reject' | 'accept_cover' | 'decline_cover' | 'nominate'
type Filter = 'all' | 'mine' | 'pending' | 'approved' | 'rejected'

function matches(request: AttendanceRequest, filter: Filter) {
  if (filter === 'mine') return request.canDecide || request.canRespondCover
  if (filter === 'pending') return request.status === 'pending' || request.status === 'awaiting_cover'
  if (filter === 'approved' || filter === 'rejected') return request.status === filter
  return true
}

/** Requests as an equal-height card grid, with a status filter (no counts on the filter). */
export function RequestsView({ requests, own, timezone, disabled, incomingCover, heading, headingMeta, clearAction, onDecision }: {
  requests: AttendanceRequest[]
  own: boolean
  timezone: string
  disabled: boolean
  incomingCover: AttendanceRequest[]
  heading: string
  headingMeta: string
  clearAction?: ReactNode
  onDecision: (request: AttendanceRequest, action: DecisionAction) => void
}) {
  const [filter, setFilter] = useState<Filter>('all')
  const tabs: { key: Filter; label: string }[] = [
    { key: 'all', label: 'All' },
    ...(own ? [] : [{ key: 'mine' as const, label: 'Needs my decision' }]),
    { key: 'pending', label: 'Pending' },
    { key: 'approved', label: 'Approved' },
    { key: 'rejected', label: 'Rejected' },
  ]
  const active = tabs.some((tab) => tab.key === filter) ? filter : 'all'
  const visible = requests.filter((request) => matches(request, active))
  return <div className="att-stack">
    {incomingCover.length > 0 && <section className="att-section" aria-label="Cover requests for you">
      <CardHead title="Colleagues asking you to cover" meta="If you accept, you agree to do your colleague's work while they are away." />
      <div className="att-req-grid">{incomingCover.map((request) => <RequestCard key={request.id} request={request} timezone={timezone} disabled={disabled} onDecision={(action) => onDecision(request, action)} />)}</div>
    </section>}
    <section className="att-section" aria-label={heading}>
      <CardHead title={heading} meta={headingMeta} action={clearAction} />
      <div className="att-filter-row"><Tabs variant="pill" tabs={tabs} active={active} onChange={(key) => setFilter(key as Filter)} /></div>
      {!visible.length
        ? <Panel flush><EmptyState title="No requests in this view" description={requests.length ? 'Choose another status to see the rest.' : own ? 'Your permission, leave and overtime requests appear here after submission.' : 'Employee requests and their approval history will appear here.'} /></Panel>
        : <div className="att-req-grid">{visible.map((request) => <RequestCard key={request.id} request={request} timezone={timezone} disabled={disabled} onDecision={(action) => onDecision(request, action)} />)}</div>}
    </section>
  </div>
}

function RequestCard({ request, timezone, disabled, onDecision }: { request: AttendanceRequest; timezone: string; disabled: boolean; onDecision: (action: DecisionAction) => void }) {
  const notes = [
    request.emergencyOverride && 'An admin allowed one extra permission. Your free minutes for the month still apply.',
    request.coverNote && `Cover response: ${request.coverNote}`,
    request.status === 'approved' && request.coverStatus === 'accepted' && !request.coverStartedAt && 'The cover person takes over on the leave date.',
    request.coverStartedAt && `Cover started ${stamp(request.coverStartedAt, timezone)}${request.coverSettledAt ? ` and ended ${stamp(request.coverSettledAt, timezone)}` : ' and is still going'}.`,
  ].filter(Boolean) as string[]
  return <article className="att-req" data-status={request.status}>
    <header>
      <RecordIdentity name={request.name} detail={`${dayLabel(request.date)} · ${requestLabels[request.kind]}${request.minutes > 0 ? ` · ${request.minutes} min` : ''}`} />
      <RequestStatus status={request.status} />
    </header>
    <p className="att-req-reason">{request.reason}</p>
    {request.convertedFromPaidLeave && <p className="att-callout att-callout-warn">Recorded as unpaid leave because the monthly paid-leave allowance is already used.</p>}
    {request.coverName && <p className="att-req-cover"><Users size={14} aria-hidden="true" /><span>Cover: <strong>{request.coverName}</strong> · {readableStatus(request.coverStatus ?? 'requested')}</span></p>}
    {notes.map((note) => <p key={note} className="att-help">{note}</p>)}
    <ol className="att-track" aria-label="Approval stages">
      {request.requiredStages.map((stage) => {
        const result = request.approvals[stage]
        const state = result ? (result.approved ? 'approved' : 'rejected') : 'waiting'
        return <li key={stage} data-state={state}>
          <span className="att-track-bar" aria-hidden="true" />
          <strong>{state === 'approved' ? <Check size={13} aria-hidden="true" /> : state === 'rejected' ? <X size={13} aria-hidden="true" /> : <CircleDashed size={13} aria-hidden="true" />}{approvalLabel(stage)}</strong>
          <span>{result ? `${result.approved ? 'Approved' : 'Rejected'} by ${result.name}` : 'Waiting for a decision'}</span>
          {result && <small>{stamp(result.at, timezone)}</small>}
          {result?.reason && <p>{result.reason}</p>}
        </li>
      })}
    </ol>
    <footer>
      <small>Requested {stamp(request.createdAt, timezone)}</small>
      <div className="att-actions">
        {request.canNominateCover && <Button size="sm" disabled={disabled} onClick={() => onDecision('nominate')}>Choose a cover colleague</Button>}
        {request.canRespondCover && <><Button size="sm" disabled={disabled} onClick={() => onDecision('decline_cover')}>Decline cover</Button><Button size="sm" variant="primary" disabled={disabled} onClick={() => onDecision('accept_cover')}>Accept cover</Button></>}
        {request.canDecide && <><Button size="sm" variant="dangerGhost" disabled={disabled} onClick={() => onDecision('reject')}>Reject</Button><Button size="sm" variant="primary" disabled={disabled} onClick={() => onDecision('approve')}>Approve</Button></>}
      </div>
    </footer>
  </article>
}
