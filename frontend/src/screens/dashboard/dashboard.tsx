import { useMemo, type ComponentProps } from 'react'
import { useNavigate } from 'react-router-dom'
import { Building2, BriefcaseBusiness, ClipboardPenLine, Database, ListChecks, Mail, Plane, UserCheck, Users } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { SummaryCards } from '@/shared/ui/workspace'
import { cardSpeech } from '@/features/dashboard/card-text'
import { ReminderBanner } from '@/features/daily-report/reminder-banner'
import { useReportData } from '@/features/reports/report-api'
import type { LeadSummaryResponse } from '@/features/leads/lead-api'
import { DashboardGraphs } from '@/features/dashboard/components/source-graph'
import { candidatesAfterPrescreening, inView } from '@/features/dashboard/summary'
import { inboxCard, type InboxCardData } from '@/features/dashboard/inbox-card'
import { jobRequirementsCard, type JobRequirementsCardData } from '@/features/dashboard/job-requirements-card'
import { useTravelled } from '@/features/travelled/travelled-api'
import { EMPTY_FILTERS } from '@/features/travelled/travelled-model'
import '@/styles/dashboard-simple.css'
import '@/styles/touch-targets.css'
import { isAdminLevel } from '@/shared/model/role-labels'

type Tone = 'blue' | 'green' | 'amber' | 'violet' | 'red'
interface Card { key: string; label: string; value: number | null; note: string; detail?: string; icon: typeof Users; tone: Tone; to: string; danger?: boolean }

const count = (value: number | null) => value === null ? '–' : value.toLocaleString('en-IN')
const noun = (value: number | null, one: string, many: string) => value === 1 ? one : many

/** Nine numbers and one graph. Everything is live: the workspace refreshes itself when anything changes, the inbox badge is
 *  polled, and the lead total is re-read whenever the lead sheet changes. Counts follow the viewer's role. */
export default function DashboardPage() {
  const store = useStore()
  const navigate = useNavigate()
  const isTeam = isAdminLevel(store.me.role)
  // Candidates ingested from email (with how many need review and how many accounts feed them). A server that does not send the
  // intake numbers yet keeps the old card: the total number of messages across the connected accounts.
  const mail = useReportData<InboxCardData>('/inbox?limit=1', store.me.id, '', ['mail', 'documents'], { noisyTopics: ['documents'], noisyGapMs: 10_000 })
  const inbox = inboxCard(mail.data)
  const requirements = jobRequirementsCard(useReportData<JobRequirementsCardData>('/job-requirements?status=active&limit=1', store.me.id, '', ['job_requirements']).data)
  const leads = useReportData<LeadSummaryResponse>(`/leads/summary?scope=${isTeam ? 'all' : 'mine'}`, store.me.id, '', ['leads'])

  // Admin and MD see the whole team; staff see their own candidates and tasks.
  const candidates = useMemo(() => inView(store.candidates, { id: store.me.id, isTeam }), [store.candidates, isTeam, store.me.id])
  // A candidate counts once they have been put through to prescreening or any later stage of an order.
  const afterPrescreening = useMemo(() => candidatesAfterPrescreening(candidates.map(candidate => candidate.id), store.applications), [candidates, store.applications])
  const { open, overdue } = useMemo(() => {
    const now = Date.now()
    const rows = store.visibleTasks.filter(task => !task.completedAt && !task.cancelledAt)
    return { open: rows.length, overdue: rows.filter(task => task.dueAt && Date.parse(task.dueAt) < now).length }
  }, [store.visibleTasks])
  const leadTotal = leads.data ? leads.data.total : null
  // Travelled: everyone who has been deployed. Same number as its screen (Follow-up has no card: it is part of Tasks).
  const travelled = useTravelled(EMPTY_FILTERS, store.me.id)
  const travelledTotal = travelled.data ? travelled.data.total : null

  const cards: Card[] = [
    { key: 'inbox', label: 'Inbox', value: inbox.value, note: inbox.note, detail: inbox.detail, icon: Mail, tone: 'blue', to: '/inbox' },
    { key: 'requirements', label: 'Job requirements', value: requirements.value, note: requirements.note, danger: requirements.danger, icon: ClipboardPenLine, tone: 'blue', to: '/job-requirements' },
    { key: 'clients', label: 'Clients', value: store.clients.length, note: `${noun(store.clients.length, 'client', 'clients')} created`, icon: Building2, tone: 'green', to: '/clients' },
    { key: 'jobs', label: 'Jobs', value: store.orders.length, note: `${noun(store.orders.length, 'job', 'jobs')} created`, icon: BriefcaseBusiness, tone: 'amber', to: '/jobs' },
    { key: 'candidates', label: 'Candidates', value: afterPrescreening, note: 'from prescreening onward', icon: UserCheck, tone: 'violet', to: '/mapped' },
    { key: 'leads', label: 'Lead sheet', value: leadTotal, note: `${noun(leadTotal, 'lead', 'leads')} on the sheet`, icon: Users, tone: 'blue', to: '/leads' },
    { key: 'databank', label: 'Data Bank', value: candidates.length, note: `${noun(candidates.length, 'resume', 'resumes')} stored`, icon: Database, tone: 'blue', to: '/data-bank' },
    { key: 'tasks', label: 'Tasks', value: open, note: overdue > 0 ? `${count(overdue)} overdue` : 'open, none overdue', danger: overdue > 0, icon: ListChecks, tone: overdue > 0 ? 'red' : 'blue', to: '/tasks' },
    { key: 'travelled', label: 'Travelled', value: travelledTotal, note: travelledTotal === null ? 'loading' : `${noun(travelledTotal, 'candidate', 'candidates')} deployed`, icon: Plane, tone: 'green', to: '/travelled' },
  ]

  // Icon, label and number only, like the Travelled totals; the notes ("3 need review", "84 overdue") are the tooltip and the spoken name.
  const items: ComponentProps<typeof SummaryCards>['items'] = cards.map(card => {
    const text = cardSpeech(card.label, count(card.value), card.note, card.detail)
    return { label: card.label, value: count(card.value), icon: card.icon, tone: card.danger ? 'red' : card.tone, danger: card.danger, href: card.to, onOpen: () => navigate(card.to), title: text, ariaLabel: text }
  })

  return <div className="dashref dashsimple">
    <ReminderBanner />
    <header className="ds-head">
      <h1>Dashboard</h1>
    </header>
    {leads.error && <div role="alert" className="ds-error">{leads.error}<button type="button" onClick={leads.refresh}>Try again</button></div>}
    <div className="ds-summary"><SummaryCards items={items} /></div>
    <DashboardGraphs candidates={candidates} anchor={leads.data ? String(leads.data.total) : ''} onOpen={() => navigate('/data-bank')} />
  </div>
}
