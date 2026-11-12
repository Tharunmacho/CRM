import { useCallback, useEffect, useRef, useState } from 'react'
import { Download, FileText, Loader2, Printer, RefreshCw, Save, Settings2 } from 'lucide-react'
import { ApiError, fileHref } from '@/shared/api/client'
import { liveInterval } from '@/shared/state/live'
import { Button, Field, Input } from '@/shared/ui/primitives'
import { FileLink } from '@/shared/ui/file-drop'
import { loadCandidateInvoice, reconcileCandidateInvoice, saveCandidateFee, type CandidateInvoiceState } from '../invoice-api'
import { billingFieldLabel, feePaise, feePreview, invoiceMoney } from '../invoice-model'
import { InvoiceSettingsPanel } from './invoice-settings-panel'
import { CandidatePaymentsPanel } from './candidate-payments'
import '@/styles/candidate-invoice.css'

const STATUS_TEXT: Record<CandidateInvoiceState['status'], string> = {
  pending_assignment: 'Choose a job', pending_ticket: 'Waiting for air ticket', pending_fee: 'Service fee needed',
  pending_configuration: 'Billing setup needed', pending_generation: 'Invoice pending', issued: 'Issued',
}

export function InvoiceCard({ candidateId, applicationId, canWork, canConfigure, onChanged }: {
  candidateId: string; applicationId?: string | null; canWork: boolean; canConfigure: boolean; onChanged: () => void
}) {
  const [state, setState] = useState<CandidateInvoiceState | null>(null)
  const [fee, setFee] = useState('')
  const [edited, setEdited] = useState(false)
  const editedRef = useRef(edited); editedRef.current = edited
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [conflict, setConflict] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const context = `${candidateId}:${applicationId ?? ''}`
  const contextRef = useRef(context); contextRef.current = context
  const changed = useRef(onChanged); changed.current = onChanged
  const reading = useRef(0)
  const writing = useRef(false)
  const attempted = useRef('')
  const previousInvoice = useRef<string | null>(null)
  const editVersion = useRef<number | null>(null)
  const linkedContext = useRef('')

  const accept = useCallback((value: CandidateInvoiceState, key: string) => {
    if (contextRef.current !== key) return
    setState(value)
    const issuedId = value.status === 'issued' ? value.invoice?.id ?? null : null
    if (issuedId && issuedId !== previousInvoice.current) changed.current()
    previousInvoice.current = issuedId
  }, [])

  const refresh = useCallback(async () => {
    if (!canWork || writing.current) return
    const request = ++reading.current
    try { const value = await loadCandidateInvoice(candidateId, applicationId); if (request === reading.current && !writing.current) { accept(value, context); if (!editedRef.current) setError('') } }
    catch (cause) { if (request === reading.current && contextRef.current === context) setError(cause instanceof Error ? cause.message : 'Invoice details could not be loaded.') }
  }, [candidateId, applicationId, canWork, context, accept])

  useEffect(() => {
    setState(null); setFee(''); setEdited(false); setError(''); setConflict(false); setBusy(false); setSettingsOpen(false)
    attempted.current = ''; previousInvoice.current = null; writing.current = false
    editVersion.current = null
    void refresh()
    const stop = canWork ? liveInterval(() => refresh(), 30_000, ['documents', 'applications', 'candidates', 'invoice_settings', 'invoices']) : () => {}
    return () => { reading.current++; stop() }
  }, [refresh, canWork])

  useEffect(() => {
    if (state && (!edited || state.status === 'issued')) { setFee(state.serviceFee ?? ''); if (state.status === 'issued') { setEdited(false); editVersion.current = null } }
  }, [state?.serviceFee, state?.status, edited])

  useEffect(() => {
    if (!state || window.location.hash !== '#af-invoice' || linkedContext.current === context) return
    linkedContext.current = context
    document.getElementById('af-invoice')?.scrollIntoView({ block: 'start' })
  }, [state, context])

  const reconcile = useCallback(async () => {
    if (!canWork || writing.current) return
    writing.current = true; reading.current++; setBusy(true); setError('')
    try { accept(await reconcileCandidateInvoice(candidateId, applicationId), context) }
    catch (cause) { if (contextRef.current === context) setError(cause instanceof Error ? cause.message : 'The invoice could not be prepared. Try again.') }
    finally { if (contextRef.current === context) { writing.current = false; setBusy(false) } }
  }, [candidateId, applicationId, canWork, context, accept])

  // Existing tickets become eligible when their actual fee or billing setup is saved.
  // Reconciliation is idempotent on the server and never advances a recruitment stage.
  useEffect(() => {
    if (!state || state.status === 'issued' || state.status === 'pending_assignment' || state.error || !state.ticketUploaded || !state.serviceFee || state.missingConfiguration.length || writing.current) return
    const key = `${context}:${state.lockVersion}:${state.status}`
    if (attempted.current === key) return
    attempted.current = key
    void reconcile()
  }, [state, context, reconcile])

  const parsed = feePaise(fee)
  const cgstRate = state?.status === 'issued' ? state.invoice?.cgstRate ?? '9.00' : state?.taxRates?.cgst ?? state?.invoice?.cgstRate ?? '9.00'
  const sgstRate = state?.status === 'issued' ? state.invoice?.sgstRate ?? '9.00' : state?.taxRates?.sgst ?? state?.invoice?.sgstRate ?? '9.00'
  const preview = feePreview(fee, cgstRate, sgstRate)
  const canEdit = canWork && state?.canEdit === true && state.status !== 'issued'
  const willIssue = Boolean(state?.ticketUploaded && !state.missingConfiguration.length)
  const save = async () => {
    if (!state || !canEdit || parsed === null || writing.current) return
    writing.current = true; reading.current++; setBusy(true); setError('')
    try {
      const value = await saveCandidateFee(candidateId, state.applicationId, (parsed / 100).toFixed(2), editVersion.current ?? state.lockVersion)
      if (contextRef.current !== context) return
      accept(value, context); setFee(value.serviceFee ?? ''); setEdited(false); setConflict(false); editVersion.current = null; changed.current()
    } catch (cause) { if (contextRef.current === context) { setError(cause instanceof Error ? cause.message : 'The service fee was not saved.'); setConflict(cause instanceof ApiError && cause.isConflict) } }
    finally { if (contextRef.current === context) { writing.current = false; setBusy(false) } }
  }
  const serverInvoice = state?.invoice
  const savedAmounts = serverInvoice && [serverInvoice.subtotal, serverInvoice.cgst, serverInvoice.sgst, serverInvoice.total].every((value) => value !== null && Number.isFinite(Number(value)))
    ? { subtotal: Math.round(Number(serverInvoice.subtotal) * 100), cgst: Math.round(Number(serverInvoice.cgst) * 100), sgst: Math.round(Number(serverInvoice.sgst) * 100), total: Math.round(Number(serverInvoice.total) * 100) } : null
  const amounts = edited ? preview : savedAmounts ?? preview
  const issued = state?.status === 'issued'
  const status = state ? STATUS_TEXT[state.status] : 'Loading'
  const message = !canWork ? 'Invoice details are available to the assigned recruiter and managers.'
    : !state ? 'Loading invoice details…'
    : state.status === 'pending_assignment' ? 'Choose the candidate’s client and job before saving a service fee.'
    : issued ? 'The PDF is saved with this candidate’s documents and included in file downloads.'
    : state.error || (state.status === 'pending_configuration' ? `Complete billing setup: ${state.missingConfiguration.map(billingFieldLabel).join(', ')}.`
      : state.ticketUploaded ? 'Air ticket received. Save the actual service fee to create the invoice automatically.'
      : 'Save the actual fee now. The invoice is created automatically when the air ticket is uploaded.')

  return <section className="af-invoice" id="af-invoice" aria-labelledby="af-invoice-title" data-invoice-status={state?.status ?? 'loading'}>
    <header className="af-invoice-heading">
      <div><span className="af-invoice-icon"><FileText aria-hidden="true" /></span><div><h3 id="af-invoice-title">Candidate invoice</h3><p>{issued && serverInvoice?.number ? `Invoice ${serverInvoice.number}` : 'Service fee and GST'}</p></div></div>
      <div className="af-invoice-heading-actions">{canWork && state && <span className={`af-invoice-status${issued ? ' is-issued' : ''}`}>{status}</span>}{canConfigure && <Button size="sm" variant="ghost" icon={<Settings2 />} onClick={() => setSettingsOpen((value) => !value)} disabled={busy}>Billing settings</Button>}</div>
    </header>
    <p className="af-invoice-hint" role="status">{message}</p>
    {state?.issuer && <p className="af-invoice-hint">{state.issuer.companyName}{state.issuer.gstin ? ` · GSTIN ${state.issuer.gstin}` : ''}</p>}
    {error && <p className="af-note is-error" role="alert">{error} {(!state || conflict) && <button type="button" disabled={busy} onClick={() => { if (conflict) { setEdited(false); editVersion.current = null; setConflict(false) }; void refresh() }}>{conflict ? 'Reload saved fee' : 'Retry'}</button>}</p>}
    {state && canWork && state.status !== 'pending_assignment' && <div className="af-invoice-body">
      <div className="af-invoice-fee">
        <Field label="Service fee before GST (₹)" hint={issued ? 'The issued invoice amount is fixed.' : 'Actual fee for this candidate, ₹5,000–₹30,000.'} error={edited && fee !== '' && parsed === null ? 'Enter ₹5,000–₹30,000, with up to two decimal places.' : undefined}>
          <Input aria-label="Service fee before GST" type="text" inputMode="decimal" value={fee} readOnly={!canEdit} disabled={busy} placeholder="Enter actual service fee" onChange={(event) => { editVersion.current ??= state.lockVersion; setFee(event.target.value); setEdited(true) }} />
        </Field>
        {canEdit && <Button variant="primary" disabled={busy || conflict || parsed === null || (!edited && state.serviceFee !== null)} icon={busy ? <Loader2 className="animate-spin" /> : <Save />} onClick={() => void save()}>{busy ? 'Saving…' : willIssue ? 'Save fee and generate invoice' : 'Save service fee'}</Button>}
      </div>
      {amounts && <dl className="af-invoice-totals" aria-label={edited ? 'Invoice amount preview' : 'Invoice amounts'}>
        <div><dt>Service fee</dt><dd>{invoiceMoney(amounts.subtotal)}</dd></div><div><dt>CGST · {Number(cgstRate)}%</dt><dd>{invoiceMoney(amounts.cgst)}</dd></div><div><dt>SGST · {Number(sgstRate)}%</dt><dd>{invoiceMoney(amounts.sgst)}</dd></div><div className="af-invoice-total"><dt>{edited ? 'Total preview' : 'Total'}</dt><dd>{invoiceMoney(amounts.total)}</dd></div>
      </dl>}
    </div>}
    {issued && serverInvoice?.downloadUrl && <div className="af-invoice-actions"><a className="af-invoice-link" href={fileHref(serverInvoice.downloadUrl)} target="_blank" rel="noopener noreferrer"><Printer aria-hidden="true" />Preview or print</a><FileLink url={serverInvoice.downloadUrl} download className="af-invoice-link" title={`Download invoice ${serverInvoice.number ?? ''}`}><Download aria-hidden="true" />Download PDF</FileLink></div>}
    {state?.status === 'pending_generation' && <Button size="sm" disabled={busy} onClick={() => void reconcile()} icon={<RefreshCw />}>Retry invoice</Button>}
    {state?.applicationId && canWork && <CandidatePaymentsPanel key={`${candidateId}:${state.applicationId}`} candidateId={candidateId} applicationId={state.applicationId} revision={`${state.lockVersion}:${state.status}:${state.serviceFee ?? ''}`} />}
    {settingsOpen && canConfigure && <InvoiceSettingsPanel onClose={() => setSettingsOpen(false)} onSaved={() => { attempted.current = ''; void reconcile() }} />}
  </section>
}
