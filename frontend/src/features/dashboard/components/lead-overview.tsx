import { useRef, useState } from 'react'
import { ArrowRight, CalendarClock, FileCheck2, Lock, PhoneOff, UserCheck, UserMinus, UserPlus } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { LEAD_REPORT_TOPICS, useReportData, type DashboardLeadList, type DashboardLeads } from '@/features/reports/report-api'
import { leadStatus, type LeadTone } from '@/features/leads/lead-model'
import { Avatar, Pill } from '@/features/dashboard/components/identity'
import {
  LIST_LOOK, leadCards, leadSubline, listNote, listPath, scopeWords, whenLabel,
  type LeadCardKey, type LeadGateView, type LeadPeriod,
} from '@/features/dashboard/lead-overview'
import '@/styles/dashboard-leads.css'

const CARD_LOOK: Record<LeadCardKey, { icon: typeof UserPlus; tone: string }> = {
  newToday: { icon: UserPlus, tone: 'primary' },
  notContacted: { icon: PhoneOff, tone: 'amber' },
  followUpsDue: { icon: CalendarClock, tone: 'red' },
  resumesReceived: { icon: FileCheck2, tone: 'violet' },
  converted: { icon: UserCheck, tone: 'green' },
  unassigned: { icon: UserMinus, tone: 'amber' },
}
const PILL: Record<LeadTone, 'info' | 'warn' | 'good' | 'bad' | 'muted' | 'accent'> = { ok: 'good', warn: 'warn', bad: 'bad', info: 'info', quiet: 'muted' }

/**
 * The dashboard's "Lead sheet" section: the same card style as the Pipeline overview, role scoped by the server
 * (staff their own assigned leads, admin and manager the team or the chosen recruiter). Press a card to see the
 * first few leads behind its number; the list is read on demand, follows the live `leads` topic, and the full
 * sheet is one button away.
 */
export function LeadOverview({ leads, period, gate, personName, staffId, onOpenSheet, onOpenLead }: {
  leads: DashboardLeads
  period: LeadPeriod
  gate: LeadGateView
  /** First name of the recruiter an admin/manager narrowed the dashboard to. */
  personName?: string | null
  /** The chosen recruiter's id (admin and manager only); empty for the whole team and for staff. */
  staffId: string
  onOpenSheet: () => void
  /** Opens one lead on the Lead sheet (`/leads?lead=<id>`). */
  onOpenLead: (id: string) => void
}) {
  const store = useStore()
  const [active, setActive] = useState<LeadCardKey | null>(null)
  const panel = useRef<HTMLElement>(null)
  const cards = leadCards(leads, { period, gate })
  const card = cards.find(item => item.key === active)
  const list = useReportData<DashboardLeadList>(active ? listPath(active, period, staffId) : null, store.me.id, '', LEAD_REPORT_TOPICS)
  const showOwner = leads.scope !== 'mine'
  const look = active ? LIST_LOOK[active] : null
  const items = list.data && list.data.card === active ? list.data.items : null
  const note = list.data && items ? listNote(list.data.total, items.length) : null

  function choose(key: LeadCardKey) {
    const opening = active !== key
    setActive(opening ? key : null)
    if (opening) window.setTimeout(() => panel.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 40)
  }

  return <section aria-label="Lead sheet">
    <header className="dr-section-head"><div><h2>Lead sheet</h2><p>People who answered your ads, for {scopeWords(leads, personName)}. Press a card to see who is behind the number.</p></div>
      <button type="button" className="dr-link" onClick={onOpenSheet}>Open Lead sheet<ArrowRight size={14} aria-hidden="true" /></button></header>
    <div className="dr-grid-leads">{cards.map(item => {
      const visual = CARD_LOOK[item.key]
      const pressed = active === item.key
      return <button key={item.key} type="button" title={item.hint} aria-pressed={pressed} aria-controls="lead-panel" className={`dr-stat dr-tone-${visual.tone}${pressed ? ' is-selected' : ''}`} onClick={() => choose(item.key)}>
        <span className="dr-stat-top"><span>{item.label}</span><span className="dr-icon-circle"><visual.icon size={17} strokeWidth={1.9} /></span></span>
        <span className="dr-stat-value">{item.value.toLocaleString('en')}</span>
        <span className={`dr-stat-detail${item.locked ? ' is-lock' : item.warn ? ' is-warn' : ''}`}>{item.locked && <Lock size={12} aria-hidden="true" />}{item.detail}</span>
      </button>
    })}</div>
    {active && card && look && <section ref={panel} id="lead-panel" className="dr-card dr-overview-panel dl-panel" aria-label={`${look.title} leads`}>
      <header className="dr-card-head"><div><h2>{look.title}</h2><p>{card.hint}</p></div>
        <span className="dl-panel-actions"><button type="button" className="dr-link" onClick={onOpenSheet}>Open Lead sheet</button><button type="button" className="dr-link" onClick={() => setActive(null)}>Hide list</button></span></header>
      {list.error && !items && <p role="alert" className="dl-empty">{list.error}</p>}
      {!items && !list.error && <p role="status" className="dl-empty">Loading leads…</p>}
      {items && items.length === 0 && <p className="dl-empty">{look.empty}</p>}
      {items && items.length > 0 && <ul className={`dl-list${showOwner ? ' has-owner' : ''}`} aria-label={`${look.title}, ${list.data?.total ?? items.length} leads`}>
        <li className="dl-head" aria-hidden="true"><span>Lead</span><span>Status</span>{showOwner && <span>Assigned to</span>}<span>{look.when}</span><span className="dl-num">Calls</span></li>
        {items.map(item => {
          const status = leadStatus(item.status)
          return <li key={item.id}>
            <button type="button" className="dr-person dl-person" aria-label={`Open ${item.name} on the Lead sheet`} onClick={() => onOpenLead(item.id)}><Avatar name={item.name} /><span><strong>{item.name}</strong><small>{leadSubline(item)}</small></span></button>
            <span><Pill tone={PILL[status.tone]}>{status.label}</Pill></span>
            {showOwner && <span className="dl-owner">{item.ownerName ? item.ownerName.split(' ')[0] : <em>Not assigned</em>}</span>}
            <span className="dl-when" title={item.at ? new Date(item.at).toLocaleString() : undefined}>{whenLabel(item.at)}</span>
            <span className="dl-num">{item.callCount || ''}</span>
          </li>
        })}
      </ul>}
      {note && <p className="dr-overview-note">{note}</p>}
    </section>}
  </section>
}
