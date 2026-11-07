import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowUpRight, Building2, BriefcaseBusiness, Database, ListChecks, Mail, UserCheck, Users } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { useReportData } from '@/features/reports/report-api'
import type { LeadSummaryResponse } from '@/features/leads/lead-api'
import { DashboardGraphs } from '@/features/dashboard/components/source-graph'
import { candidatesAfterPrescreening, inView } from '@/features/dashboard/summary'
import '@/styles/dashboard-simple.css'

type Tone = 'blue' | 'green' | 'amber' | 'violet' | 'teal' | 'slate' | 'red'
interface Card { key: string; label: string; value: number | null; note: string; icon: typeof Users; tone: Tone; to: string; danger?: boolean }

const count = (value: number | null) => value === null ? '–' : value.toLocaleString('en-IN')
const noun = (value: number | null, one: string, many: string) => value === 1 ? one : many

/** Seven numbers and one graph. Everything is live: the workspace refreshes itself when anything changes, the inbox badge is
 *  polled, and the lead total is re-read whenever the lead sheet changes. Counts follow the viewer's role. */
export default function DashboardPage() {
  const store = useStore()
  const navigate = useNavigate()
  const isTeam = store.me.role === 'admin' || store.me.role === 'manager'
  // Emails across every connected email account: the total number of messages in their inboxes, so adding an account raises it.
  const mail = useReportData<{ accounts: { messageCount: number; disconnectedAt?: string | null }[] }>('/inbox?limit=1', store.me.id, '', ['mail'])
  const accounts = mail.data ? mail.data.accounts.filter(account => !account.disconnectedAt) : null
  const emails = accounts ? accounts.reduce((sum, account) => sum + (account.messageCount ?? 0), 0) : null
  const leads = useReportData<LeadSummaryResponse>(`/leads/summary?scope=${isTeam ? 'all' : 'mine'}`, store.me.id, '', ['leads'])

  // Admin and manager see the whole team; staff see their own candidates and tasks.
  const candidates = useMemo(() => inView(store.candidates, { id: store.me.id, isTeam }), [store.candidates, isTeam, store.me.id])
  // A candidate counts once they have been put through to prescreening or any later stage of an order.
  const afterPrescreening = useMemo(() => candidatesAfterPrescreening(candidates.map(candidate => candidate.id), store.applications), [candidates, store.applications])
  const { open, overdue } = useMemo(() => {
    const now = Date.now()
    const rows = store.visibleTasks.filter(task => !task.completedAt && !task.cancelledAt)
    return { open: rows.length, overdue: rows.filter(task => task.dueAt && Date.parse(task.dueAt) < now).length }
  }, [store.visibleTasks])
  const leadTotal = leads.data ? leads.data.total : null

  const cards: Card[] = [
    { key: 'inbox', label: 'Inbox', value: emails, note: !accounts ? 'loading emails' : accounts.length === 0 ? 'no email account added' : `${noun(emails, 'email', 'emails')} · ${accounts.length} ${noun(accounts.length, 'account', 'accounts')}`, icon: Mail, tone: 'blue', to: '/inbox' },
    { key: 'clients', label: 'Clients', value: store.clients.length, note: `${noun(store.clients.length, 'client', 'clients')} created`, icon: Building2, tone: 'green', to: '/clients' },
    { key: 'jobs', label: 'Jobs', value: store.orders.length, note: `${noun(store.orders.length, 'job', 'jobs')} created`, icon: BriefcaseBusiness, tone: 'amber', to: '/jobs' },
    { key: 'candidates', label: 'Candidates', value: afterPrescreening, note: 'from prescreening onward', icon: UserCheck, tone: 'violet', to: '/mapped' },
    { key: 'leads', label: 'Lead sheet', value: leadTotal, note: `${noun(leadTotal, 'lead', 'leads')} on the sheet`, icon: Users, tone: 'teal', to: '/leads' },
    { key: 'databank', label: 'Data Bank', value: candidates.length, note: `${noun(candidates.length, 'resume', 'resumes')} stored`, icon: Database, tone: 'slate', to: '/data-bank' },
    { key: 'tasks', label: 'Tasks', value: open, note: overdue > 0 ? `${count(overdue)} overdue` : 'open, none overdue', danger: overdue > 0, icon: ListChecks, tone: 'red', to: '/tasks' },
  ]

  return <div className="dashref dashsimple">
    <header className="ds-head">
      <h1>Dashboard</h1>
    </header>
    {leads.error && <div role="alert" className="ds-error">{leads.error}<button type="button" onClick={leads.refresh}>Try again</button></div>}
    <div className="ds-grid">{cards.map(card => <button key={card.key} type="button" className={`ds-card ds-${card.tone}`} onClick={() => navigate(card.to)}>
      <span className="ds-top"><span className="ds-icon"><card.icon size={20} strokeWidth={1.9} /></span><ArrowUpRight size={16} className="ds-go" aria-hidden="true" /></span>
      <span className="ds-label">{card.label}</span>
      <span className="ds-value">{count(card.value)}</span>
      <span className={`ds-note${card.danger ? ' is-danger' : ''}`}>{card.note}</span>
    </button>)}</div>
    <DashboardGraphs candidates={candidates} anchor={leads.data ? String(leads.data.total) : ''} onOpen={() => navigate('/data-bank')} />
  </div>
}
