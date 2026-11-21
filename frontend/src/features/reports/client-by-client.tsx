import { Fragment, useState, type ReactNode } from 'react'
import { Building2, ChevronDown, ChevronRight, FileSpreadsheet, Loader2, RefreshCw, TriangleAlert, X } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { agencyDay } from '@/shared/lib/task-time'
import { Button, SearchInput } from '@/shared/ui/primitives'
import {
  ORDER_STATUS_WORDS, STATUS_TABS, STATUS_TONE, STATUS_WORDS, activityText, rateText, stageGroups, tabCounts,
  type ClientLine, type ClientOrder, type StatusTab,
} from './client-summary-model'
import '@/styles/report-mgmt-tabs.css'
import '@/styles/client-tracker.css'

const DASH = <span className="rm-dash" aria-label="none">{'–'}</span>
const widths = [86, 98, 104, 84, 142, 132, 104]

/**
 * The by-client table: a row per client (orders, openings still to fill, candidates in process, placed, fill rate, last activity, status). A client
 * opens to its orders (filled and open) and, under each order, the candidates by stage. Searching and the status tabs only narrow the rows.
 */
export function ClientsPanel({ clients, all, tab, onTab, query, onQuery, today, zone, stalledDays, loading, error, refreshing, onRefresh, exporting, onExport, switcher }: {
  clients: ClientLine[]
  /** Every client, unfiltered: the tabs' totals. */
  all: ClientLine[]
  tab: StatusTab
  onTab: (tab: StatusTab) => void
  query: string
  onQuery: (value: string) => void
  /** The agency's current day and time zone: the last activity is told in days of the agency's own calendar. */
  today: string
  zone: string
  stalledDays: number
  loading: boolean
  error: string
  refreshing: boolean
  onRefresh: () => void
  exporting: boolean
  onExport: () => void
  switcher: ReactNode
}) {
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set())
  const counts = tabCounts(all)
  const toggle = (id: string) => setOpen((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next })
  const narrowed = tab !== 'all' || query.trim() !== ''
  return <section className="rm-panel cm" aria-label="Clients" aria-busy={loading || refreshing || undefined}>
    <div className="rm-head">
      <div><h2><Building2 size={16} aria-hidden="true" /> Clients</h2></div>
      {switcher}
    </div>
    <div className="rm-toolbar">
      <SearchInput className="rm-search" value={query} onChange={onQuery} placeholder="Search client, country, order or position" />
      <div className="rm-tabs" role="tablist" aria-label="Client status">
        {STATUS_TABS.map((entry) => <button key={entry.key} type="button" role="tab" aria-selected={tab === entry.key} className={cn('rm-tab', tab === entry.key && 'is-on')} onClick={() => onTab(entry.key)}>
          {entry.label}<span className="rm-tab-count">{counts[entry.key]}</span>
        </button>)}
      </div>
      <div className="rm-tools cm-tools">
        {narrowed && <Button size="sm" variant="ghost" icon={<X size={14} />} onClick={() => { onTab('all'); onQuery('') }}>Clear</Button>}
        <Button size="sm" icon={<RefreshCw size={14} className={refreshing ? 'rm-spin' : undefined} />} disabled={refreshing} onClick={onRefresh}>{refreshing ? 'Refreshing' : 'Refresh'}</Button>
        <Button size="sm" variant="secondary" disabled={loading || exporting || !clients.length} icon={exporting ? <Loader2 size={14} className="rm-spin" /> : <FileSpreadsheet size={14} />} onClick={onExport}
          title="Download the clients shown, with their orders and candidates, as an Excel file">{exporting ? 'Preparing Excel…' : 'Export Excel'}</Button>
      </div>
    </div>

    {loading && !all.length ? <div className="rm-state"><p role="status">Loading the clients…</p></div>
      : error && !all.length ? <div className="rm-state"><TriangleAlert size={22} aria-hidden="true" /><p role="alert" className="rm-state-title">{error}</p><p>It will try again by itself, or press Refresh.</p></div>
        : !all.length ? <div className="rm-state" role="status"><p className="rm-state-title">No clients yet</p><p>Clients appear here once an order is placed for them.</p></div>
          : !clients.length ? <div className="rm-state" role="status"><p className="rm-state-title">No clients match</p><p>{tab === 'stalled' ? `No active client has been quiet for ${stalledDays} days.` : 'Change the search or the tab.'}</p></div>
            : <div className="rm-scroll is-cards" role="region" aria-label="Clients table, scroll sideways for more columns" tabIndex={0}>
              <table className="rm-table is-cards cm-table" aria-label="Clients">
                <colgroup><col />{widths.map((width, i) => <col key={i} style={{ width: width + 'px' }} />)}</colgroup>
                <thead><tr>
                  <th scope="col" className="is-left">Client</th>
                  <th scope="col">Orders</th>
                  <th scope="col" title="Openings still to fill on open orders, after those already placed">Openings</th>
                  <th scope="col" title="Candidates mapped to the client's orders who have not travelled yet">In process</th>
                  <th scope="col" title="Candidates who have travelled">Placed</th>
                  <th scope="col" title="Placed over the openings the orders asked for">Fill rate</th>
                  <th scope="col" title="The last time an order or a candidate on it changed">Last activity</th>
                  <th scope="col">Status</th>
                </tr></thead>
                <tbody>
                  {clients.map((client, index) => {
                    const expanded = open.has(client.id)
                    const detailId = `cm-detail-${client.id}`
                    const last = client.lastActivity ? agencyDay(client.lastActivity, zone) : null
                    return <Fragment key={client.id}>
                      <tr className={cn(index % 2 === 1 && 'is-alt', expanded && 'is-picked', client.status === 'stalled' && 'is-stalled')}>
                        <th scope="row" className="is-left">
                          <button type="button" className="cm-toggle" aria-expanded={expanded} aria-controls={detailId} onClick={() => toggle(client.id)}
                            title={expanded ? 'Hide its orders and candidates' : 'Show its orders and candidates'}>
                            {expanded ? <ChevronDown size={16} aria-hidden="true" /> : <ChevronRight size={16} aria-hidden="true" />}
                            <span className="cm-name"><span className="rm-who">{client.name}</span>{client.country && <small className="rm-sub">{client.country}</small>}</span>
                          </button>
                        </th>
                        <td data-label="Orders" className="tnum"><span className="cm-two"><strong>{client.orderCount}</strong><small>{client.openOrders} open</small></span></td>
                        <td data-label="Openings" className="tnum">{client.openings > 0 ? <strong>{client.openings}</strong> : DASH}</td>
                        <td data-label="In process" className="tnum">{client.inProcess > 0 ? <strong>{client.inProcess}</strong> : DASH}</td>
                        <td data-label="Placed" className="tnum">{client.placed > 0 ? <strong>{client.placed}</strong> : DASH}</td>
                        <td data-label="Fill rate" className="tnum">{client.fillRate == null ? DASH : <span className="cm-rate"><strong>{rateText(client.fillRate)}</strong>
                          <span className="cm-meter" role="meter" aria-label={`${client.name} fill rate`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={client.fillRate}><i style={{ width: `${Math.min(100, client.fillRate)}%` }} /></span></span>}</td>
                        <td data-label="Last activity" title={last ?? undefined}><span className={cn('cm-two', client.stalled && 'is-warn')}><strong>{activityText(last, today)}</strong></span></td>
                        <td data-label="Status"><span className={cn('rm-pill', `tone-${STATUS_TONE[client.status]}`)}>{STATUS_WORDS[client.status]}</span></td>
                      </tr>
                      {expanded && <tr className="cm-detail" id={detailId}><td colSpan={widths.length + 1}><OrderDetail client={client} /></td></tr>}
                    </Fragment>
                  })}
                </tbody>
              </table>
            </div>}
  </section>
}

/** A client's orders (filled and open) and, under each, the candidates by stage. */
function OrderDetail({ client }: { client: ClientLine }) {
  return <div className="cm-orders">
    <table className="rm-table cm-orders-table" aria-label={`Orders of ${client.name}`}>
      <colgroup><col style={{ width: '140px' }} /><col /><col style={{ width: '104px' }} /><col style={{ width: '96px' }} /><col style={{ width: '84px' }} /><col style={{ width: '74px' }} /><col style={{ width: '74px' }} /><col style={{ width: '86px' }} /></colgroup>
      <thead><tr>
        <th scope="col" className="is-left">Order</th><th scope="col" className="is-left">Position</th><th scope="col">Country</th><th scope="col">Status</th>
        <th scope="col">Openings</th><th scope="col">Placed</th><th scope="col">Open</th><th scope="col">In process</th>
      </tr></thead>
      <tbody>
        {client.orders.map((order) => <OrderRows key={order.id} order={order} />)}
      </tbody>
    </table>
  </div>
}

function OrderRows({ order }: { order: ClientOrder }) {
  const groups = stageGroups(order.candidates)
  return <>
    <tr className="cm-order-row">
      <th scope="row" className="is-left tnum">{order.ref}</th>
      <td className="is-left">{order.position || DASH}</td>
      <td>{order.country || DASH}</td>
      <td><span className={cn('rm-pill', `tone-${order.isOpen ? 'green' : order.status === 'filled' ? 'blue' : 'neutral'}`)}>{ORDER_STATUS_WORDS[order.status] ?? order.status}</span></td>
      <td className="tnum">{order.openings}</td>
      <td className="tnum">{order.placed || DASH}</td>
      <td className={cn('tnum', order.open > 0 && 'rm-warn')}>{order.open || DASH}</td>
      <td className="tnum">{order.inProcess || DASH}</td>
    </tr>
    <tr className="cm-stage-row">
      <td colSpan={8}>{groups.length
        ? <ul className="cm-stages" aria-label={`Candidates on ${order.ref} by stage`}>{groups.map((group) => <li key={group.key} className={cn(group.key === 'travelled' && 'is-travelled')}><b>{group.label}</b><span className="cm-stage-count tnum">{group.names.length}</span><span className="cm-stage-names">{group.names.join(', ')}</span></li>)}</ul>
        : <span className="cm-nobody">No candidates on this order yet.</span>}</td>
    </tr>
  </>
}
