import { useRef, useState, type ReactNode } from 'react'
import { ArrowRight, Hourglass, PlaneTakeoff, ScrollText, Stamp } from 'lucide-react'
import { DataTable, type Column } from '@/shared/ui/table'
import { ApplicationFormButton } from '@/features/application-form/components/application-form-button'
import type { DashboardOverview, OverviewItem } from '@/features/reports/report-api'
import { Avatar, Flag, Pill, StageMini } from '@/features/dashboard/components/identity'
import { Stack, press } from '@/features/dashboard/components/stacked'
import { cappedNote, defaultView, overviewCards, overviewRows, overviewTabs, waitLabel, type OverviewKey, type OverviewView } from '@/features/dashboard/overview'

const CARD_LOOK: Record<OverviewKey, { icon: typeof Hourglass; tone: string; title: string }> = {
  offer: { icon: ScrollText, tone: 'primary', title: 'Offer letter' },
  visa: { icon: Stamp, tone: 'violet', title: 'Visa' },
  departure: { icon: PlaneTakeoff, tone: 'green', title: 'Departure' },
  idle: { icon: Hourglass, tone: 'amber', title: 'Idle stage' },
}
/** A wait this long is shown in the warning colour in the idle list. */
const LONG_WAIT_DAYS = 14

export interface Place { city?: string | null; country?: string | null }

function StageCell({ item }: { item: OverviewItem }) {
  if (item.status === 'deployed') return <Pill tone="good">Deployed</Pill>
  if (item.status === 'closed') return <Pill tone="muted" title={`Closed at stage ${item.stage}`}>Closed</Pill>
  return <StageMini no={item.stage} />
}
const placeText = (place: Place) => [place.city, place.country].filter(Boolean).join(', ') || 'Not added'

/** The four "Pipeline overview" cards and, under the pressed one, the candidates behind its number. */
export function PipelineOverview({ overview, scope, showOwner, narrow, placeOf, onOpen }: {
  overview: DashboardOverview
  /** Who the numbers are for, e.g. "the whole team", "Asha" or "you". */
  scope: string
  showOwner: boolean
  narrow: boolean
  placeOf: (orderId: string) => Place
  onOpen: (item: OverviewItem) => void
}) {
  const [active, setActive] = useState<OverviewKey | null>(null)
  const [views, setViews] = useState<Partial<Record<OverviewKey, OverviewView>>>({})
  const panel = useRef<HTMLElement>(null)
  const cards = overviewCards(overview)
  const card = cards.find(item => item.key === active)
  const view = active ? views[active] ?? defaultView(overview, active) : 'all'
  const rows = active ? overviewRows(overview, active, view) : []
  const tabs = active ? overviewTabs(overview, active) : []
  const note = active ? cappedNote(overview, active) : null

  function choose(key: OverviewKey) {
    const opening = active !== key
    setActive(opening ? key : null)
    if (opening) window.setTimeout(() => panel.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 40)
  }
  const waitClass = (item: OverviewItem) => item.state === 'current' && (active === 'idle' ? item.days >= LONG_WAIT_DAYS : false) ? 'dr-overdue' : ''
  const actions = (item: OverviewItem): ReactNode => <span className="dr-row-actions">
    <button type="button" className="dr-open" aria-label={`Open ${item.candidate}`} onClick={event => { event.stopPropagation(); onOpen(item) }}>Open<ArrowRight size={14} /></button>
    <ApplicationFormButton candidateId={item.candidateId} orderId={item.orderId} size="sm" label="Form" />
  </span>

  const columns: Column<OverviewItem>[] = [
    { key: 'candidate', header: 'Candidate', value: row => row.candidate, width: '16%', render: row => <div className="dr-person"><Avatar name={row.candidate} /><div><strong>{row.candidate}</strong><span>{row.company}</span></div></div> },
    { key: 'position', header: 'Position / order', value: row => row.designation, width: '16%', render: row => <div className="dr-copy"><strong>{row.designation || 'No position'}</strong><small>{row.order} · {row.client}</small></div> },
    { key: 'country', header: 'Country', value: row => placeOf(row.orderId).country ?? '', width: '13%', render: row => <span className="dr-place"><Flag country={placeOf(row.orderId).country} />{placeText(placeOf(row.orderId))}</span> },
    { key: 'stage', header: 'Stage', value: row => row.stage, width: '16%', render: row => <StageCell item={row} /> },
    ...(showOwner ? [{ key: 'owner', header: 'Recruiter', value: (row: OverviewItem) => row.owner, width: '10%', render: (row: OverviewItem) => <span className="dr-owner"><Avatar name={row.owner || '?'} size="sm" />{row.owner ? row.owner.split(' ')[0] : 'Not assigned'}</span> }] : []),
    { key: 'days', header: active === 'idle' || view === 'current' ? 'Waiting' : 'When', value: row => row.days, width: '10%', render: row => <span className={waitClass(row)} title={new Date(row.since).toLocaleDateString()}>{waitLabel(row)}</span> },
    { key: 'actions', header: 'Actions', align: 'center', width: '18%', render: actions },
  ]
  const empty = <div className="dr-empty is-small"><span><Hourglass size={18} strokeWidth={1.5} /></span><strong>{active === 'idle' ? 'No one is stuck' : 'No candidates here'}</strong><p>{active === 'idle' ? `Everyone has moved within ${overview.idleDays} days.` : 'Candidates will show here when they reach this step.'}</p></div>

  return <section aria-label="Pipeline overview">
    <header className="dr-section-head"><div><h2>Pipeline overview</h2><p>Offer letters, visas, departures and stalled candidates for {scope}. Press a card to see who is behind the number.</p></div></header>
    <div className="dr-grid-4">{cards.map(item => {
      const look = CARD_LOOK[item.key]
      const pressed = active === item.key
      return <button key={item.key} type="button" title={item.hint} aria-pressed={pressed} aria-controls="overview-panel" className={`dr-stat dr-tone-${look.tone}${pressed ? ' is-selected' : ''}`} onClick={() => choose(item.key)}>
        <span className="dr-stat-top"><span>{item.label}</span><span className="dr-icon-circle"><look.icon size={17} strokeWidth={1.9} /></span></span>
        <span className="dr-stat-value">{item.value.toLocaleString('en')}</span>
        <span className={`dr-stat-detail${item.warn ? ' is-warn' : ''}`}>{item.detail}</span>
      </button>
    })}</div>
    {active && card && <section ref={panel} id="overview-panel" className="dr-card dr-overview-panel" aria-label={`${CARD_LOOK[active].title} candidates`}>
      <header className="dr-card-head"><div><h2>{CARD_LOOK[active].title}</h2><p>{card.hint}</p></div><button type="button" className="dr-link" onClick={() => setActive(null)}>Hide list</button></header>
      {tabs.length > 0 && <div className="dr-tabs dr-tabs-scroll" role="group" aria-label={`Filter ${CARD_LOOK[active].title.toLowerCase()} candidates`}>{tabs.map(tab => <button key={tab.view} type="button" aria-pressed={view === tab.view} className={view === tab.view ? 'is-active' : ''} onClick={() => setViews({ ...views, [active]: tab.view })}>{tab.label}<span className="dr-tab-count">{tab.count.toLocaleString('en')}</span></button>)}</div>}
      {narrow
        ? <Stack key={`${active}:${view}`} total={rows.length} step={6} label={`${CARD_LOOK[active].title} candidates`} empty={empty}>{limit => rows.slice(0, limit).map(item => <li key={item.applicationId} className="dr-mcard" {...press(() => onOpen(item))}>
          <div className="dr-mcard-top"><div className="dr-person"><Avatar name={item.candidate} /><div><strong>{item.candidate}</strong><span>{item.designation || 'No position'} · {item.order}</span></div></div><StageCell item={item} /></div>
          <dl>
            <div><dt>Client</dt><dd>{item.client || 'Not added'}</dd></div>
            <div><dt>Country</dt><dd><span className="dr-place"><Flag country={placeOf(item.orderId).country} />{placeText(placeOf(item.orderId))}</span></dd></div>
            {showOwner && <div><dt>Recruiter</dt><dd>{item.owner || 'Not assigned'}</dd></div>}
            <div><dt>{item.state === 'current' ? 'Waiting' : 'When'}</dt><dd className={waitClass(item)}>{waitLabel(item)}</dd></div>
          </dl>
          {actions(item)}
        </li>)}</Stack>
        : <DataTable rows={rows} columns={columns} rowKey={row => row.applicationId} pageSize={8} resetPageKey={`${active}:${view}`} onRowClick={onOpen} empty={empty} />}
      {note && <p className="dr-overview-note">{note}</p>}
    </section>}
  </section>
}
