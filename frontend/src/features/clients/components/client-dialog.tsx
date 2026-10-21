import { useState } from 'react'
import { api } from '@/shared/api/client'
import type { Client } from '@/shared/model/types'
import { useStore } from '@/shared/state/crm-store'
import { Button, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { refreshMasterData } from '@/features/master-data/components/record-audit'

export function ClientDialog({ client, onClose }: { client?: Client; onClose: () => void }) {
  const store = useStore()
  const [form, setForm] = useState({
    name: client?.name ?? '', email: client?.email ?? '', phone: client?.phone ?? '',
    whatsapp: client?.whatsapp ?? '', country: client?.country ?? '', city: client?.city ?? '',
    notes: client?.notes ?? '', status: client?.status ?? 'active',
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const set = (key: string, value: string) => setForm((previous) => ({ ...previous, [key]: value }))
  const close = () => { if (!saving) onClose() }
  const save = async () => {
    if (saving) return
    setSaving(true); setError('')
    try {
      if (client) await api.put('/clients/' + client.id, form)
      else await api.post('/clients', form)
      await refreshMasterData(store, client ? 'Client updated' : 'Client created')
      onClose()
    } catch (err) { setError(err instanceof Error ? err.message : 'Client could not be saved. Please try again.') }
    finally { setSaving(false) }
  }
  return <Dialog open onClose={close} width="lg" title={client ? 'Edit client' : 'New client'}
    description="Your contact person. Their companies and recruitment orders stay linked."
    footer={<><Button variant="ghost" disabled={saving} onClick={close}>Cancel</Button><Button variant="primary" type="submit" form="client-form" disabled={saving}>{saving ? 'Saving…' : 'Save client'}</Button></>}>
    {error && <p role="alert" className="mb-4 rounded-control bg-stamp-50 p-3 text-sm text-stamp-700">{error}</p>}
    <form id="client-form" onSubmit={(event) => { event.preventDefault(); void save() }}>
      <fieldset disabled={saving} className="grid gap-4 sm:grid-cols-2">
        <Field label="Full name" required className="sm:col-span-2"><Input required maxLength={200} value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="Khalid Rahman" /></Field>
        <Field label="Email"><Input type="email" maxLength={255} value={form.email} onChange={(e) => set('email', e.target.value)} /></Field>
        <Field label="Status" hint="Inactive contacts cannot receive new companies or open orders."><Select value={form.status} onChange={(e) => set('status', e.target.value)}><option value="active">Active</option><option value="inactive">Inactive</option></Select></Field>
        <Field label="Phone"><Input type="tel" maxLength={40} value={form.phone} onChange={(e) => set('phone', e.target.value)} /></Field>
        <Field label="WhatsApp"><Input type="tel" maxLength={40} value={form.whatsapp} onChange={(e) => set('whatsapp', e.target.value)} /></Field>
        <Field label="City"><Input maxLength={100} value={form.city} onChange={(e) => set('city', e.target.value)} /></Field>
        <Field label="Country"><Input maxLength={100} value={form.country} onChange={(e) => set('country', e.target.value)} /></Field>
        <Field label="Notes" className="sm:col-span-2"><Textarea rows={3} maxLength={20000} value={form.notes} onChange={(e) => set('notes', e.target.value)} /></Field>
      </fieldset>
    </form>
  </Dialog>
}
