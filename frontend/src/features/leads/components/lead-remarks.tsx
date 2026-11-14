import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, BellRing, CircleDot, Download, ExternalLink, FileUp, ListChecks, MessageCircle, MessageSquareText, Pencil, PhoneCall, Pin, RefreshCw, StickyNote, Undo2, UserCheck, UserPlus } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { Sheet } from '@/shared/ui/overlay'
import { Button } from '@/shared/ui/primitives'
import { Avatar } from '@/features/candidates/components/db-parts'
import { leadApi, type LeadDetail } from '../lead-api'
import { createdParts, leadStatus, type Lead } from '../lead-model'
import { relativeTime, remarkCards, remarkStats, timelineDays, type RemarkCard, type TimelineEvent, type TimelineIcon } from '../lead-remarks-model'

const ICONS: Record<TimelineIcon, typeof PhoneCall> = {
  call: PhoneCall, note: StickyNote, status: ListChecks, assign: UserCheck, import: Download, convert: UserPlus, unconvert: Undo2,
  resume: FileUp, edit: Pencil, whatsapp: MessageCircle, followup: BellRing, other: CircleDot,
}

const clock = (value: string) => new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
const exact = (value: string) => new Date(value).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })
const dateOnly = (value?: string | null) => { const parts = createdParts(value); return parts ? `${parts.day} ${parts.year}` : '' }

function Chip({ tone, children }: { tone: string; children: string }) {
  return <span className={cn('ld-rm-chip', `is-${tone}`)}>{children}</span>
}

function Stat({ label, value }: { label: string; value: string }) {
  return <div className="ld-rm-stat"><dt>{label}</dt><dd>{value}</dd></div>
}

function Card({ card, now }: { card: RemarkCard; now: Date }) {
  const who = card.who || 'Staff'
  const label = card.kind === 'pinned' ? 'Notes on file' : card.kind === 'note' ? 'Note' : ''
  return (
    <li className={cn('ld-rm-card', card.kind === 'pinned' && 'is-pinned')}>
      <div className="ld-rm-card-head">
        <Avatar name={who} size="sm" />
        <div className="ld-rm-card-who">
          <strong>{who}</strong>
          {card.at && <time dateTime={card.at} title={exact(card.at)}>{relativeTime(card.at, now)}</time>}
        </div>
        {card.kind === 'pinned' && <span className="ld-rm-pin"><Pin size={12} aria-hidden="true" />{label}</span>}
        {card.kind === 'note' && <Chip tone="quiet">{label}</Chip>}
        {card.outcome && <Chip tone={card.outcome.tone}>{card.outcome.label}</Chip>}
      </div>
      <p className="ld-rm-text">{card.text}</p>
    </li>
  )
}

function Event({ event }: { event: TimelineEvent }) {
  const Icon = ICONS[event.icon]
  return (
    <li className="ld-rm-event">
      <span className={cn('ld-rm-icon', `is-${event.tone}`)} aria-hidden="true"><Icon size={14} /></span>
      <div className="ld-rm-event-body">
        <p className="ld-rm-event-title">{event.title}</p>
        {event.detail && <p className="ld-rm-event-detail">{event.detail}</p>}
        <p className="ld-rm-event-meta">{event.who || 'System'}<span aria-hidden="true"> · </span><time dateTime={event.at} title={exact(event.at)}>{clock(event.at)}</time></p>
      </div>
    </li>
  )
}

function Loading() {
  return (
    <div className="ld-rm-skel" role="status" aria-label="Loading remarks">
      <i className="is-strip" /><i className="is-title" /><i className="is-card" /><i className="is-card" /><i className="is-title" /><i className="is-row" /><i className="is-row" />
    </div>
  )
}

interface Props {
  /** The row whose "Assigned to" cell was clicked; null keeps the panel closed. */
  lead: Lead | null
  ownerName: string
  onClose: () => void
  /** Opens the normal lead drawer for this lead. */
  onOpenLead: (lead: Lead) => void
}

/**
 * Side panel behind the "Assigned to" cell: the recruiter's remarks as cards and a day-by-day timeline of what they
 * did on this lead. Read-only; "Open full lead" leads to the drawer for any change.
 */
export function LeadRemarksSheet({ lead, ownerName, onClose, onOpenLead }: Props) {
  const [detail, setDetail] = useState<LeadDetail | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const id = lead?.id

  const load = useCallback(async (signal: AbortSignal) => {
    if (!id) return
    try {
      const data = await leadApi.detail(id, signal)
      if (!signal.aborted) { setDetail(data); setError('') }
    } catch (e) { if (!signal.aborted) setError(e instanceof Error ? e.message : 'We could not load the remarks.') }
  }, [id])

  useEffect(() => { setDetail(null); setError('') }, [id])
  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal)
    return () => controller.abort()
  }, [load, attempt])

  const now = useMemo(() => new Date(), [detail, id]) // eslint-disable-line react-hooks/exhaustive-deps
  const activities = detail?.activities ?? []
  const cards = useMemo(() => remarkCards(activities, detail?.notes), [detail]) // eslint-disable-line react-hooks/exhaustive-deps
  const days = useMemo(() => timelineDays(activities, now), [detail, now]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!lead) return null
  const shown: Lead = detail ?? lead
  const status = leadStatus(shown.status)
  const stats = remarkStats(activities, cards, shown.callCount)
  const owner = ownerName || 'Staff'
  const assigned = dateOnly(shown.assignedAt)

  return (
    <Sheet open onClose={onClose} title={shown.name || 'Lead'} description={`Assigned to ${owner}`} width="md"
      footer={<Button variant="secondary" icon={<ExternalLink className="h-4 w-4" />} onClick={() => onOpenLead(lead)}>Open full lead</Button>}>
      <div className="ld-rm">
        <div className="ld-rm-strip">
          <div className="ld-rm-owner">
            <Avatar name={owner} />
            <div>
              <strong>{owner}</strong>
              <small>{assigned ? `Assigned ${assigned}` : 'Assigned date not recorded'}</small>
            </div>
            <span className={cn('db-tone', `is-${status.tone}`, 'ld-rm-status')}>{status.label}</span>
          </div>
          <dl className="ld-rm-stats">
            <Stat label="Calls" value={String(stats.calls)} />
            <Stat label="Remarks" value={String(stats.remarks)} />
            <Stat label="Last contacted" value={shown.lastContactedAt ? relativeTime(shown.lastContactedAt, now) || '–' : 'Not yet'} />
          </dl>
        </div>

        {error && !detail ? (
          <div className="ld-rm-error" role="alert">
            <AlertTriangle size={18} aria-hidden="true" />
            <div><strong>We could not load the remarks</strong><p>{error}</p></div>
            <Button variant="secondary" icon={<RefreshCw className="h-4 w-4" />} onClick={() => setAttempt((value) => value + 1)}>Try again</Button>
          </div>
        ) : !detail ? <Loading /> : (
          <>
            <section className="ld-rm-sec" aria-labelledby="ld-rm-remarks">
              <h3 id="ld-rm-remarks"><MessageSquareText size={15} aria-hidden="true" />Remarks<span className="ld-rm-count">{cards.length}</span></h3>
              {cards.length === 0
                ? <p className="ld-rm-empty"><strong>No remarks yet</strong>When {owner} adds remarks after a call, they appear here.</p>
                : <ul className="ld-rm-cards">{cards.map((card) => <Card key={card.id} card={card} now={now} />)}</ul>}
            </section>

            <section className="ld-rm-sec" aria-labelledby="ld-rm-timeline">
              <h3 id="ld-rm-timeline"><CircleDot size={15} aria-hidden="true" />Timeline<span className="ld-rm-count">{activities.length}</span></h3>
              {days.length === 0
                ? <p className="ld-rm-empty"><strong>Nothing has been done on this lead yet</strong>Calls, status changes and uploads will show up here as they happen.</p>
                : days.map((day) => (
                  <div className="ld-rm-day" key={day.key}>
                    <h4>{day.label}</h4>
                    <ol className="ld-rm-events">{day.events.map((event) => <Event key={event.id} event={event} />)}</ol>
                  </div>
                ))}
            </section>
          </>
        )}
      </div>
    </Sheet>
  )
}
