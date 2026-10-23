import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import type { Company, FileVersion } from '@/shared/model/types'
import { newestFileVersions } from '@/shared/lib/document-files'
import { useStore } from '@/shared/state/crm-store'
import { Button, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { FileDrop } from '@/shared/ui/file-drop'
import { refreshMasterData } from '@/features/master-data/components/record-audit'

export function CompanyDialog({ company, clientId, onClose }: { company?: Company; clientId?: string; onClose: () => void }) {
  const store = useStore()
  const [form, setForm] = useState({
    clientId: company?.clientId ?? clientId ?? '', name: company?.name ?? '', country: company?.country ?? '',
    city: company?.city ?? '', industry: company?.industry ?? '', contactPerson: company?.contactPerson ?? '',
    contactEmail: company?.contactEmail ?? '', phone: company?.phone ?? '', whatsapp: company?.whatsapp ?? '', address: company?.address ?? '',
    notes: company?.notes ?? '', status: company?.status ?? 'active',
  })
  const [savedId, setSavedId] = useState(company?.id)
  const [agreement, setAgreement] = useState<File | null>(null)
  const [versions, setVersions] = useState<FileVersion[]>([])
  const [historyError, setHistoryError] = useState('')
  const [historyLoading, setHistoryLoading] = useState(false)
  const [retry, setRetry] = useState(0)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const set = (key: string, value: string) => setForm((previous) => ({ ...previous, [key]: value }))
  const close = () => { if (!saving) onClose() }
  useEffect(() => {
    if (!savedId) return
    const controller = new AbortController()
    setHistoryLoading(true); setHistoryError('')
    api.get<(FileVersion & { slot: string })[]>('/files?entity_type=company&entity_id=' + encodeURIComponent(savedId), { signal: controller.signal })
      .then((files) => setVersions(newestFileVersions(files.filter((file) => file.slot === 'agreement'))))
      .catch((err) => { if (!controller.signal.aborted) setHistoryError(err instanceof Error ? err.message : 'Could not load agreement history') })
      .finally(() => { if (!controller.signal.aborted) setHistoryLoading(false) })
    return () => controller.abort()
  }, [savedId, retry])
  const save = async () => {
    if (saving) return
    if (!form.clientId) { setError('Choose a client for this company.'); return }
    setSaving(true); setError('')
    let identifier = savedId
    let detailsSaved = false
    try {
      const record = identifier ? await api.put<Company>('/companies/' + identifier, form) : await api.post<Company>('/companies', form)
      identifier = record.id
      setSavedId(identifier); detailsSaved = true
      if (agreement) {
        await api.upload('company', identifier, 'agreement', agreement)
        setAgreement(null)
      }
      await refreshMasterData(store, company ? 'Company updated' : 'Company created')
      onClose()
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Please try again.'
      setError(detailsSaved ? 'Company details are saved. The agreement was not uploaded. Retry Save company, or close and upload it later. ' + message : message)
      if (detailsSaved) await refreshMasterData(store, 'Company details saved')
    } finally { setSaving(false) }
  }
  const latest = versions[0] ?? (company?.agreement ? { ...company.agreement, sizeKb: 0 } : null)
  return <Dialog open onClose={close} width="lg" title={company ? 'Edit company' : savedId ? 'Finish company agreement' : 'New company'}
    description="The employer belongs to a client contact. Its agreements and hiring orders stay together."
    footer={<><Button variant="ghost" disabled={saving} onClick={close}>{savedId && error ? 'Close and finish later' : 'Cancel'}</Button><Button type="submit" form="company-form" variant="primary" disabled={saving}>{saving ? 'Saving…' : 'Save company'}</Button></>}>
    {error && <p role="alert" className="mb-4 rounded-control bg-stamp-50 p-3 text-sm text-stamp-700">{error}</p>}
    <form id="company-form" onSubmit={(event) => { event.preventDefault(); void save() }}>
      <fieldset disabled={saving} className="grid gap-4 sm:grid-cols-2">
        <Field label="Client contact" required hint="The client relationship this employer belongs to" className="sm:col-span-2"><Select value={form.clientId} onChange={(e) => set('clientId', e.target.value)}><option value="">Choose a client contact</option>{store.clients.filter((client) => client.status === 'active' || client.id === form.clientId).map((client) => <option key={client.id} value={client.id}>{client.name}{client.status === 'inactive' ? ' (inactive)' : ''}</option>)}</Select></Field>
        <Field label="Company name" required className="sm:col-span-2"><Input required maxLength={200} value={form.name} onChange={(e) => set('name', e.target.value)} /></Field>
        <Field label="Industry"><Input maxLength={120} value={form.industry} onChange={(e) => set('industry', e.target.value)} /></Field>
        <Field label="Status"><Select value={form.status} onChange={(e) => set('status', e.target.value)}><option value="active">Active</option><option value="inactive">Inactive</option></Select></Field>
        <Field label="City"><Input maxLength={100} value={form.city} onChange={(e) => set('city', e.target.value)} /></Field>
        <Field label="Country"><Input maxLength={100} value={form.country} onChange={(e) => set('country', e.target.value)} /></Field>
        <Field label="Contact person"><Input maxLength={160} value={form.contactPerson} onChange={(e) => set('contactPerson', e.target.value)} /></Field>
        <Field label="Contact email"><Input type="email" maxLength={255} value={form.contactEmail} onChange={(e) => set('contactEmail', e.target.value)} /></Field>
        <Field label="Phone"><Input type="tel" maxLength={40} value={form.phone} onChange={(e) => set('phone', e.target.value)} /></Field>
        <Field label="WhatsApp"><Input type="tel" maxLength={40} value={form.whatsapp} onChange={(e) => set('whatsapp', e.target.value)} /></Field>
        <Field label="Address" className="sm:col-span-2"><Textarea rows={2} maxLength={2000} value={form.address} onChange={(e) => set('address', e.target.value)} /></Field>
        <div className="sm:col-span-2">
          <FileDrop label="Agreement" value={agreement ? { name: agreement.name, sizeKb: Math.max(1, Math.round(agreement.size / 1024)) } : latest}
            versions={versions} disabled={saving} uploaderName={(id) => store.user(id)?.name ?? 'Team member'}
            help="Choose an agreement, then Save company to upload it. Each replacement keeps the previous version."
            onFile={(file) => setAgreement(file)} />
          {agreement && <Button size="sm" variant="ghost" onClick={() => setAgreement(null)}>Remove selected upload</Button>}
          {historyLoading && <p className="mt-2 text-xs text-ink-500" role="status">Loading agreement history…</p>}
          {historyError && <div role="alert" className="mt-2 flex flex-wrap items-center gap-2 text-xs text-stamp-700"><span>{historyError}</span><Button size="sm" onClick={() => setRetry((value) => value + 1)}>Retry history</Button></div>}
        </div>
        <Field label="Notes" className="sm:col-span-2"><Textarea rows={3} maxLength={20000} value={form.notes} onChange={(e) => set('notes', e.target.value)} /></Field>
      </fieldset>
    </form>
  </Dialog>
}
