import { useEffect, useState } from 'react'
import { Loader2, Save, X } from 'lucide-react'
import { Button, Field, Input, Textarea } from '@/shared/ui/primitives'
import { loadInvoiceSettings, saveInvoiceSettings, type InvoiceSettingsFields, type InvoiceSettingsState } from '../invoice-api'
import { billingFieldLabel } from '../invoice-model'

export function InvoiceSettingsPanel({ onSaved, onClose }: { onSaved: () => void; onClose: () => void }) {
  const [state, setState] = useState<InvoiceSettingsState | null>(null)
  const [draft, setDraft] = useState<InvoiceSettingsFields | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    let active = true
    setError('')
    void loadInvoiceSettings().then((value) => { if (active) { setState(value); setDraft(value.settings) } })
      .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : 'Billing settings could not be loaded.') })
    return () => { active = false }
  }, [retry])
  const change = <K extends keyof InvoiceSettingsFields>(key: K, value: InvoiceSettingsFields[K]) => setDraft((current) => current ? { ...current, [key]: value } : current)
  const save = async () => {
    if (!draft || !state?.canEdit || busy) return
    setBusy(true); setError('')
    try { const saved = await saveInvoiceSettings(draft, state.lockVersion); setState(saved); setDraft(saved.settings); onSaved(); onClose() }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Billing settings were not saved.') }
    finally { setBusy(false) }
  }
  return <section className="af-invoice-settings" aria-label="Billing settings">
    <header><div><h4>Billing settings</h4><p>These details appear on new invoices. Issued invoices keep their original details.</p></div><Button variant="ghost" size="sm" disabled={busy} onClick={onClose} icon={<X />}>Close</Button></header>
    {error && <p className="af-note is-error" role="alert">{error}{!state && <Button size="sm" onClick={() => setRetry((value) => value + 1)}>Retry</Button>}</p>}
    {!draft ? !error && <p role="status">Loading billing settings…</p> : <>
      {state?.missing.length ? <p className="af-invoice-hint">Complete: {state.missing.map(billingFieldLabel).join(', ')}.</p> : null}
      <div className="af-invoice-settings-grid">
        <Field label="Company name" required><Input value={draft.companyName} disabled={busy || !state?.canEdit} onChange={(event) => change('companyName', event.target.value)} /></Field>
        <Field label="GSTIN" required><Input value={draft.gstin} maxLength={15} disabled={busy || !state?.canEdit} onChange={(event) => change('gstin', event.target.value.toUpperCase())} /></Field>
        <Field label="Company address" required className="af-invoice-wide"><Textarea value={draft.address} rows={2} disabled={busy || !state?.canEdit} onChange={(event) => change('address', event.target.value)} /></Field>
        <Field label="Invoice prefix" hint="For example, CI/26-27/."><Input value={draft.invoicePrefix} disabled={busy || !state?.canEdit} onChange={(event) => change('invoicePrefix', event.target.value)} /></Field>
        <Field label="Next invoice number" required><Input type="number" min={1} step={1} value={draft.nextNumber ?? ''} disabled={busy || !state?.canEdit} onChange={(event) => change('nextNumber', event.target.value === '' ? null : Number(event.target.value))} /></Field>
        <Field label="SAC code"><Input value={draft.sacCode} disabled={busy || !state?.canEdit} onChange={(event) => change('sacCode', event.target.value)} /></Field>
        <Field label="GST"><Input value={`${Number(draft.cgstRate)}% CGST + ${Number(draft.sgstRate)}% SGST`} readOnly /></Field>
        <Field label="Bank name"><Input value={draft.bankName} disabled={busy || !state?.canEdit} onChange={(event) => change('bankName', event.target.value)} /></Field>
        <Field label="Account number"><Input value={draft.bankAccount} disabled={busy || !state?.canEdit} onChange={(event) => change('bankAccount', event.target.value)} /></Field>
        <Field label="IFSC"><Input value={draft.bankIfsc} disabled={busy || !state?.canEdit} onChange={(event) => change('bankIfsc', event.target.value.toUpperCase())} /></Field>
      </div>
      <footer><Button variant="primary" disabled={busy || !state?.canEdit} onClick={() => void save()} icon={busy ? <Loader2 className="animate-spin" /> : <Save />}>{busy ? 'Saving…' : 'Save billing settings'}</Button></footer>
    </>}
  </section>
}
