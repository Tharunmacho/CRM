import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Hourglass, Percent, Plane, Users } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { downloadApiFile } from '@/shared/api/download'
import { useAgencyToday } from '@/features/daily-report/use-agency-today'
import { cn } from '@/shared/lib/cn'
import { SummaryCards } from '@/shared/ui/workspace'
import { useReportData } from './report-api'
import { ClientsPanel } from './client-by-client'
import { PlacementSheet, stageFromAddress } from './client-placement-sheet'
import { clientsFileName, clientsPath, filterClients, rateText, type ClientsResult, type StatusTab } from './client-summary-model'
import '@/styles/report-mgmt-tabs.css'
import '@/styles/client-tracker.css'
import '@/styles/touch-targets.css'

type View = 'clients' | 'sheet'
const VIEWS: readonly { key: View; label: string }[] = [{ key: 'clients', label: 'By client' }, { key: 'sheet', label: 'Placement sheet' }]
const NO_CLIENTS: ClientsResult['clients'] = []

/**
 * Client tracker, read from the management side. Four summary cards (openings left, candidates in process, placed, fill rate), then one of two views of the same data: "By client" (a line per client that opens to its orders and the candidates by stage, with
 * status tabs, search and Excel) or the "Placement sheet" (every placement as the clients ask for it, with its own filters and Excel). A Dashboard
 * chip (?stage=10) opens the sheet already narrowed to that stage.
 */
export function ClientTrackerReport() {
  const store = useStore()
  const notify = store.notify
  const zone = store.agencyTimezone || 'Asia/Kolkata'
  const today = useAgencyToday(zone)
  const [params] = useSearchParams()
  const [view, setView] = useState<View>(() => (stageFromAddress(params) ? 'sheet' : 'clients'))
  const [tab, setTab] = useState<StatusTab>('all')
  const [query, setQuery] = useState('')
  const [exporting, setExporting] = useState(false)
  const { data, error, refresh, refreshing } = useReportData<ClientsResult>('/reports/client-tracker/clients', store.me.id, '', ['reports', 'tasks'])

  const all = data?.clients ?? NO_CLIENTS
  const shown = useMemo(() => filterClients(all, tab, query), [all, tab, query])
  const summary = data?.summary
  const days = summary?.stalledDays ?? 14

  const cards = [
    { label: 'Openings left', value: summary?.openings ?? '–', detail: summary ? `${summary.openOrders} open ${summary.openOrders === 1 ? 'order' : 'orders'}` : ' ', icon: Hourglass, tone: 'violet' as const },
    { label: 'In process', value: summary?.inProcess ?? '–', detail: 'candidates', icon: Users, tone: 'blue' as const },
    { label: 'Placed', value: summary?.placed ?? '–', detail: 'travelled', icon: Plane, tone: 'green' as const },
    { label: 'Fill rate', value: summary ? rateText(summary.fillRate) : '–', detail: 'of openings', icon: Percent, tone: 'green' as const },
  ]

  const exportFile = async () => {
    setExporting(true)
    try { await downloadApiFile(clientsPath(tab, query, 'xlsx'), clientsFileName(today)) }
    catch (failure) { notify(failure instanceof Error ? failure.message : 'The file could not be created. Please try again.', 'warn') }
    finally { setExporting(false) }
  }

  const switcher = <div className="rm-tabs ct-views" role="tablist" aria-label="View">
    {VIEWS.map((entry) => <button key={entry.key} type="button" role="tab" aria-selected={view === entry.key} className={cn('rm-tab', view === entry.key && 'is-on')} onClick={() => setView(entry.key)}>{entry.label}</button>)}
  </div>

  return <div className="rm ct">
    <div className="rm-cards"><SummaryCards items={cards} /></div>
    {view === 'clients'
      ? <ClientsPanel clients={shown} all={all} tab={tab} onTab={setTab} query={query} onQuery={setQuery} today={today} zone={zone} stalledDays={days}
        loading={!data && !error} error={error ?? ''} refreshing={refreshing} onRefresh={refresh} exporting={exporting} onExport={() => void exportFile()} switcher={switcher} />
      : <PlacementSheet switcher={switcher} />}
  </div>
}
