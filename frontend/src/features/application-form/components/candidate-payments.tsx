import { useRef, useState } from 'react'
import { Banknote, Check, Loader2, Plus, RotateCcw, X } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { useReportData } from '@/features/reports/report-api'
import { agencyDay } from '@/shared/lib/task-time'
import { dateLabel } from '@/shared/lib/format'
import { Button, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { DatePicker } from '@/shared/ui/date-picker'
import { candidatePaymentsPath, recordCandidatePayment, voidCandidatePayment, type CandidatePayments, type PaymentMethod } from '../payment-api'
import { invoiceMoney, paymentPaise } from '../invoice-model'

const PAYMENT_TOPICS = ['payments', 'invoices', 'applications', 'reports']
const METHODS: Record<PaymentMethod, string> = { cash: 'Cash', bank: 'Bank transfer', upi: 'UPI', cheque: 'Cheque' }
const currency = (value: string | null) => value === null ? 'Not set' : invoiceMoney(Math.round(Number(value) * 100))

export function CandidatePaymentsPanel({ candidateId, applicationId, revision }: { candidateId: string; applicationId: string; revision: string }) {
  const store = useStore()
  const { data, error: readError, refresh } = useReportData<CandidatePayments>(candidatePaymentsPath(candidateId, applicationId), store.me.id, revision, PAYMENT_TOPICS)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState({ amount: '', date: agencyDay(new Date(), store.agencyTimezone), method: '' as PaymentMethod | '', reference: '', note: '', sourceStageId: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [voiding, setVoiding] = useState<string | null>(null)
  const [voidReason, setVoidReason] = useState('')
  const requestKey = useRef<string | null>(null)
  const retryingReceipt = requestKey.current !== null
  const edit = (key: keyof typeof draft, value: string) => { requestKey.current = null; setDraft((current) => ({ ...current, [key]: value })) }
  const parsed = paymentPaise(draft.amount)
  const outstanding = data?.balance === null || data?.balance === undefined ? null : Math.round(Number(data.balance) * 100)
  const tooMuch = parsed !== null && outstanding !== null && parsed > outstanding
  const save = async () => {
    if ((!data?.canRecord && !retryingReceipt) || busy || !parsed || (tooMuch && !retryingReceipt) || !draft.date || !draft.method) return
    setBusy(true); setError('')
    requestKey.current ??= crypto.randomUUID()
    try {
      await recordCandidatePayment(candidateId, { applicationId, amount: (parsed / 100).toFixed(2), date: draft.date, method: draft.method, reference: draft.reference.trim(), note: draft.note.trim(), idempotencyKey: requestKey.current, ...(draft.sourceStageId ? { sourceStageId: draft.sourceStageId } : {}) })
      requestKey.current = null; setEditing(false); setDraft((current) => ({ ...current, amount: '', reference: '', note: '', sourceStageId: '' })); refresh()
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'The payment was not recorded. Retry with the same details.') }
    finally { setBusy(false) }
  }
  const voidPayment = async () => {
    if (!voiding || voidReason.trim().length < 3 || busy) return
    setBusy(true); setError('')
    try { await voidCandidatePayment(candidateId, voiding, voidReason.trim()); setVoiding(null); setVoidReason(''); refresh() }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'This receipt could not be voided.') }
    finally { setBusy(false) }
  }
  const confirmAdvance = (advance: CandidatePayments['availableAdvances'][number]) => {
    requestKey.current = null
    setDraft({ amount: advance.amount, date: advance.date || '', method: advance.method ?? '', reference: advance.reference ?? '', note: '', sourceStageId: advance.sourceStageId })
    setEditing(true); setError('')
  }
  return <section className="af-payments" aria-label="Candidate payments">
    <header><div><Banknote aria-hidden="true" /><h4>Payments received</h4></div>{data?.canRecord && !editing && <Button size="sm" icon={<Plus />} onClick={() => { setEditing(true); setError('') }}>Record payment</Button>}</header>
    {(readError || error) && <p className="af-note is-error" role="alert">{error || readError} {(readError || retryingReceipt) && <button type="button" disabled={busy} onClick={refresh}>{retryingReceipt ? 'Check saved payments' : 'Retry'}</button>}</p>}
    {!data && !readError && <p className="af-invoice-hint" role="status">Loading payment records…</p>}
    {data && <>
      <dl className="af-payment-summary"><div><dt>Agreed total incl. GST</dt><dd>{currency(data.total)}</dd></div><div><dt>Actually received</dt><dd>{currency(data.received)}</dd></div><div><dt>Balance to collect</dt><dd>{currency(data.balance)}</dd></div></dl>
      {Number(data.credit) > 0 && <p className="af-invoice-hint">Credit recorded: {currency(data.credit)}</p>}
      {!data.serviceFee && <p className="af-invoice-hint">Save the actual service fee before recording a payment.</p>}
      {data.total && !data.invoiceNumber && <p className="af-invoice-hint">Invoice pending. Received payments are recorded against the agreed fee and GST.</p>}
      {editing && <div className="af-payment-form">
        <header><b>{draft.sourceStageId ? 'Confirm an earlier advance was received' : 'Record money already received'}</b><Button size="sm" variant="ghost" icon={<X />} disabled={busy} onClick={() => { setEditing(false); requestKey.current = null }}>Cancel</Button></header>
        <div className="af-invoice-settings-grid">
          <Field label="Amount received (₹)" required error={tooMuch && !retryingReceipt ? 'Amount exceeds the remaining balance.' : draft.amount && !parsed ? 'Enter a positive amount with up to two decimal places.' : undefined}><Input aria-label="Amount received" inputMode="decimal" value={draft.amount} readOnly={Boolean(draft.sourceStageId)} disabled={busy} onChange={(event) => edit('amount', event.target.value)} /></Field>
          <Field label="Received date" required><DatePicker aria-label="Received date" value={draft.date} disabled={busy || Boolean(draft.sourceStageId)} max={agencyDay(new Date(), store.agencyTimezone)} onChange={(value) => edit('date', value)} /></Field>
          <Field label="Payment method" required><Select aria-label="Payment method" value={draft.method} disabled={busy || Boolean(draft.sourceStageId)} onChange={(event) => edit('method', event.target.value)}><option value="">Choose how it was received</option>{Object.entries(METHODS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</Select></Field>
          <Field label="Receipt / transaction reference"><Input value={draft.reference} readOnly={Boolean(draft.sourceStageId)} disabled={busy} maxLength={200} onChange={(event) => edit('reference', event.target.value)} /></Field>
          <Field label="Note" className="af-invoice-wide"><Textarea value={draft.note} disabled={busy} maxLength={1000} rows={2} onChange={(event) => edit('note', event.target.value)} /></Field>
        </div>
        <Button variant="primary" disabled={busy || (!data.canRecord && !retryingReceipt) || !parsed || (tooMuch && !retryingReceipt) || !draft.date || !draft.method} onClick={() => void save()} icon={busy ? <Loader2 className="animate-spin" /> : <Check />}>{busy ? 'Recording…' : retryingReceipt ? 'Retry recording receipt' : 'Record received amount'}</Button>
      </div>}
      {data.availableAdvances.length > 0 && <details className="af-payment-advances"><summary>Review {data.availableAdvances.length} earlier {data.availableAdvances.length === 1 ? 'advance' : 'advances'}</summary><p>These stage records are not included in money received until you confirm the actual receipt.</p><ul>{data.availableAdvances.map((advance) => <li key={advance.sourceStageId}><span><b>{currency(advance.amount)}</b>{advance.date ? ` · ${dateLabel(advance.date)}` : ''}{advance.method ? ` · ${METHODS[advance.method]}` : ''}</span><Button size="sm" disabled={busy || !data.canRecord} onClick={() => confirmAdvance(advance)}>Confirm receipt</Button></li>)}</ul></details>}
      {data.payments.length ? <details className="af-payment-history" open><summary>Payment history ({data.payments.length})</summary><ul>{data.payments.map((payment) => <li key={payment.id} className={payment.voidedAt ? 'is-void' : ''}>
        <div><b>{currency(payment.amount)}</b><span>{dateLabel(payment.date)} · {METHODS[payment.method]}{payment.reference ? ` · ${payment.reference}` : ''}</span>{payment.note && <p>{payment.note}</p>}<small>Recorded by {payment.createdBy.name || 'Former team member'}{payment.voidedAt ? ` · Voided: ${payment.voidReason}` : ''}</small></div>
        {data.canVoid && !payment.voidedAt && <Button size="sm" variant="ghost" disabled={busy} icon={<RotateCcw />} onClick={() => { setVoiding(payment.id); setVoidReason('') }}>Void receipt</Button>}
      </li>)}</ul></details> : <p className="af-invoice-hint">No payments have been recorded.</p>}
      {voiding && <div className="af-payment-form"><Field label="Reason for voiding this receipt" required hint="Use at least 3 characters. This corrects the record. It does not refund money."><Textarea value={voidReason} disabled={busy} minLength={3} maxLength={1000} onChange={(event) => setVoidReason(event.target.value)} /></Field><div className="af-invoice-actions"><Button variant="danger" disabled={busy || voidReason.trim().length < 3} onClick={() => void voidPayment()}>Void receipt record</Button><Button disabled={busy} onClick={() => setVoiding(null)}>Cancel</Button></div></div>}
    </>}
  </section>
}
