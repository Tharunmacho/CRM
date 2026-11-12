import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, RefreshCw, Search } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { Button, Input, Select } from '@/shared/ui/primitives'
import { applicationFormHref } from '@/features/application-form/form-model'
import { invoiceMoney } from '@/features/application-form/invoice-model'
import { useReportData } from './report-api'
import '@/styles/fee-collections.css'

interface FeeCollectionRow {
  invoiceId: string | null; candidateId: string; applicationId: string | null; name: string; ref: string; invoiceNo: string | null
  status: string; paymentStatus: string; fee: string | null; gst: string | null; total: string | null; totalInvoiced: string
  received: string; balance: string | null; credit: string; issuedAt: string | null; downloadUrl: string | null
}
interface FeeCollections {
  totals: { count: number; feeSubtotal: string; gst: string; agreedTotal: string; totalInvoiced: string; received: string; balance: string; credit: string }
  rows: FeeCollectionRow[]; total: number; limit: number; offset: number
}
const TOPICS = ['payments', 'invoices', 'reports', 'candidates']
const money = (value: string | null) => value === null ? 'Not set' : invoiceMoney(Math.round(Number(value) * 100))
const paymentLabel = (value: string) => ({ unpaid: 'Unpaid', partial: 'Part paid', paid: 'Paid', pending_fee: 'Fee not set', not_set: 'Fee not set' } as Record<string, string>)[value] ?? value.replaceAll('_', ' ')

export function FeeCollectionsReport() {
  const store = useStore()
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('all')
  const [offset, setOffset] = useState(0)
  useEffect(() => { const timer = window.setTimeout(() => { setQuery(search.trim()); setOffset(0) }, 250); return () => window.clearTimeout(timer) }, [search])
  const params = new URLSearchParams({ search: query, status, limit: '50', offset: String(offset) })
  const { data, error, refreshing, refresh } = useReportData<FeeCollections>(`/invoices/finance-summary?${params}`, store.me.id, '', TOPICS)
  return <div className="fee-collections">
    <section className="rv-card fee-collection-controls" aria-label="Fee collection filters"><label><Search aria-hidden="true" /><Input aria-label="Search candidate fees" placeholder="Search candidate or invoice" value={search} onChange={(event) => setSearch(event.target.value)} /></label><Select aria-label="Payment status" value={status} onChange={(event) => { setStatus(event.target.value); setOffset(0) }}><option value="all">All payment statuses</option><option value="unpaid">Unpaid</option><option value="partial">Part paid</option><option value="paid">Paid</option></Select><Button disabled={refreshing} onClick={refresh} icon={<RefreshCw className={refreshing ? 'animate-spin' : undefined} />}>Refresh</Button></section>
    {error && <p className="rv-alert" role="alert">{error} <button type="button" onClick={refresh}>Retry</button></p>}
    {!data && !error && <p className="fee-collection-note" role="status">Loading fee collections…</p>}
    {data && <>
      <dl className="fee-collection-totals">
        {[
          ['Service fees before GST', data.totals.feeSubtotal], ['GST', data.totals.gst], ['Agreed total incl. GST', data.totals.agreedTotal],
          ['Invoiced total', data.totals.totalInvoiced], ['Actually received', data.totals.received], ['Balance to collect', data.totals.balance],
        ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{money(value)}</dd></div>)}
      </dl>
      <p className="fee-collection-note">{data.totals.count} candidate {data.totals.count === 1 ? 'record' : 'records'} match these filters. Money received includes confirmed payments before an invoice is issued. Fees not entered remain “Not set”.{Number(data.totals.credit) > 0 ? ` Recorded credit: ${money(data.totals.credit)}.` : ''}</p>
      <section className="rv-card fee-collection-table" aria-label="Candidate fee collection records"><div className="fee-collection-scroll"><table><thead><tr><th>Candidate</th><th>Invoice</th><th>Service fee</th><th>GST</th><th>Agreed total</th><th>Invoiced</th><th>Received</th><th>Balance</th><th>Payment status</th><th><span className="sr-only">Manage</span></th></tr></thead><tbody>
        {data.rows.map((row) => <tr key={row.invoiceId ?? `${row.candidateId}:${row.applicationId ?? ''}`}><td><b>{row.name}</b><small>{row.ref}</small></td><td>{row.invoiceNo || <span className="fee-pending">Pending invoice</span>}</td><td>{money(row.fee)}</td><td>{money(row.gst)}</td><td>{money(row.total)}</td><td>{money(row.totalInvoiced)}</td><td>{money(row.received)}</td><td>{money(row.balance)}</td><td><span className={`fee-payment-status is-${row.paymentStatus}`}>{row.total === null ? 'Fee not set' : paymentLabel(row.paymentStatus)}</span></td><td><Link to={`${applicationFormHref(row.candidateId, null, row.applicationId)}#af-invoice`} className="fee-manage-link">Manage <ArrowRight aria-hidden="true" /></Link></td></tr>)}
        {!data.rows.length && <tr><td colSpan={10} className="fee-collection-empty">No candidates match these filters.</td></tr>}
      </tbody></table></div>
      <footer><span>{data.total ? `${data.offset + 1}–${Math.min(data.offset + data.rows.length, data.total)} of ${data.total}` : '0 records'}</span><div><Button size="sm" disabled={offset === 0 || refreshing} onClick={() => setOffset(Math.max(0, offset - data.limit))}>Previous</Button><Button size="sm" disabled={offset + data.rows.length >= data.total || refreshing} onClick={() => setOffset(offset + data.limit)}>Next</Button></div></footer></section>
    </>}
  </div>
}
