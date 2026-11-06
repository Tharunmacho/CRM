import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Building2, BriefcaseBusiness, Database, ListChecks, Mail, Plus, RefreshCw, UserCheck, Users } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { useReportData } from '@/features/reports/report-api'
import type { LeadSummaryResponse } from '@/features/leads/lead-api'
import { candidatesAfterPrescreening, inView } from '@/features/dashboard/summary'
import '@/styles/dashboard-simple.css'

type Tone = 'blue' | 'green' | 'amber' | 'violet' | 'teal' | 'slate' | 'red'
interface Card { key: string; label: string; value: number | null; icon: typeof Users; tone: Tone; to: string }

const count = (value: number | null) => value === null ? '–' : value.toLocaleString('en-IN')

/** Clean overview: one number per area, each opening its own page. Counts follow the viewer's role. */
export default function DashboardPage() {
  const store = useStore()
  const navigate = useNavigate()
  const isTeam = store.me.role === 'admin' || store.me.role === 'manager'
  // Everything here is live: the workspace refreshes itself when anything changes, the inbox badge is polled, and the
  // lead total is re-read whenever the lead sheet changes.
  const leads = useReportData<LeadSummaryResponse>(`/leads/summary?scope=${isTeam ? 'all' : 'mine'}`, store.me.id, '', ['leads'])

  // Admin and manager see the whole team; staff see their own candidates and tasks.
  const candidates = useMemo(() => inView(store.candidates, { id: store.me.id, isTeam }), [store.candidates, isTeam, store.me.id])
  // A candidate counts once they have been put through to prescreening or any later stage of an order.
  const afterPrescreening = useMemo(() => candidatesAfterPrescreening(candidates.map(candidate => candidate.id), store.applications), [candidates, store.applications])
  const openTasks = useMemo(() => store.visibleTasks.filter(task => !task.completedAt && !task.cancelledAt).length, [store.visibleTasks])

  const cards: Card[] = [
    { key: 'inbox', label: 'Inbox', value: store.inboxUnread, icon: Mail, tone: 'blue', to: '/inbox' },
    { key: 'clients', label: 'Clients', value: store.clients.length, icon: Building2, tone: 'green', to: '/clients' },
    { key: 'jobs', label: 'Jobs', value: store.orders.length, icon: BriefcaseBusiness, tone: 'amber', to: '/jobs' },
    { key: 'candidates', label: 'Candidates', value: afterPrescreening, icon: UserCheck, tone: 'violet', to: '/mapped' },
    { key: 'leads', label: 'Lead sheet', value: leads.data ? leads.data.total : null, icon: Users, tone: 'teal', to: '/leads' },
    { key: 'databank', label: 'Data Bank', value: candidates.length, icon: Database, tone: 'slate', to: '/data-bank' },
    { key: 'tasks', label: 'Tasks', value: openTasks, icon: ListChecks, tone: 'red', to: '/tasks' },
  ]

  return <div className="dashsimple">
    <header className="ds-head">
      <h1>Dashboard</h1>
      <div className="ds-actions">
        <button type="button" className="ds-btn ds-icon-only" aria-label="Refresh dashboard" title="Refresh" disabled={leads.refreshing} onClick={leads.refresh}><RefreshCw size={15} className={leads.refreshing ? 'is-spinning' : ''} /></button>
        <button type="button" className="ds-btn is-primary" onClick={() => navigate('/add-candidate')}><Plus size={15} />Add candidate</button>
      </div>
    </header>
    {leads.error && <div role="alert" className="ds-error">{leads.error}<button type="button" onClick={leads.refresh}>Try again</button></div>}
    <div className="ds-grid">{cards.map(card => <button key={card.key} type="button" className={`ds-card ds-${card.tone}`} onClick={() => navigate(card.to)}>
      <span className="ds-icon"><card.icon size={20} strokeWidth={1.9} /></span>
      <span className="ds-label">{card.label}</span>
      <span className="ds-value">{count(card.value)}</span>
    </button>)}</div>
  </div>
}
