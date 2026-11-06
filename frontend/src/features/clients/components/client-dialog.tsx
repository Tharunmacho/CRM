import { useMemo, useState } from 'react'
import { Building2, UserCheck, UserRound } from 'lucide-react'
import { api } from '@/shared/api/client'
import type { Client, Company } from '@/shared/model/types'
import { useStore } from '@/shared/state/crm-store'
import { Button, Field, Input, Textarea } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { SuggestInput } from '@/shared/ui/suggest-input'
import { refreshMasterData } from '@/features/master-data/components/record-audit'
import { DialogMark } from '@/features/master-data/components/dialog-mark'
import { Flag, FormSection } from '@/features/clients/components/parts'
import { StaffPicker, allocatableUsers } from '@/features/clients/components/party-fields'
import '@/styles/clients-simple.css'
import { frequentValues } from '@/shared/lib/suggestions'
import { WEBSITE_MAX, checkWebsite } from '@/shared/lib/website'

/** Country names already saved on clients, companies and orders, most used first. Any other name can still be typed. */
export function useCountryOptions() {
  const store = useStore()
  return useMemo(
    () => frequentValues([...store.clients.map((c) => c.country), ...store.companies.map((c) => c.country), ...store.orders.map((o) => o.country)]),
    [store.clients, store.companies, store.orders],
  )
}

/** A country field that suggests names as you type and shows the flag it recognises. */
export function CountryInput({ value, onChange, options }: { value: string; onChange: (value: string) => void; options: string[] }) {
  return (
    <div className="client-form-flag">
      <Flag country={value} />
      <SuggestInput wrapperClassName="min-w-0 flex-1" maxLength={100} value={value} onChange={onChange} options={options} aria-label="Country" placeholder="Start typing a country" />
    </div>
  )
}

/**
 * A client is the contact person plus the company that gives the orders. Creating saves both together (contact person, then
 * company name, website, country, city and address) and the CRM staff who look after the client. Editing changes the contact
 * person, the place and the staff; the company keeps its own details on its record (client page).
 */
export function ClientDialog({ client, onClose }: { client?: Client; onClose: () => void }) {
  const store = useStore()
  const countries = useCountryOptions()
  const [form, setForm] = useState({
    name: client?.name ?? '', designation: client?.designation ?? '', email: client?.email ?? '', phone: client?.phone ?? '',
    country: client?.country ?? '', city: client?.city ?? '',
  })
  // Company-only fields, used when a client is created.
  const [employer, setEmployer] = useState({ name: '', website: '', address: '' })
  // CRM staff who look after this client. Only people who can still be allocated are kept: the server refuses inactive users and admins.
  const staffUsers = useMemo(() => allocatableUsers(store.users), [store.users])
  const [staffIds, setStaffIds] = useState<string[]>(() => (client?.staffIds ?? []).filter((id) => staffUsers.some((user) => user.id === id)))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const set = (key: string, value: string) => setForm((previous) => ({ ...previous, [key]: value }))
  const setCompany = (key: string, value: string) => setEmployer((previous) => ({ ...previous, [key]: value }))
  const close = () => { if (!saving) onClose() }
  const creating = !client

  const save = async () => {
    if (saving) return
    const web = creating ? checkWebsite(employer.website) : { value: '', error: '' }
    if (web.error) { setError('Website: ' + web.error); return }
    setSaving(true); setError('')
    try {
      if (client) {
        // The API replaces the whole record, so what this form no longer shows is sent back as it was saved.
        await api.put('/clients/' + client.id, {
          ...form, whatsapp: client.whatsapp ?? '', status: client.status, notes: client.notes ?? '', staffContacts: client.staffContacts ?? [], staffIds,
        })
      } else {
        await api.post<Client & { company: Company }>('/clients', { ...form, staffIds, company: { name: employer.name, website: web.value, address: employer.address, country: form.country, city: form.city } })
      }
      await refreshMasterData(store, 'Client saved')
      onClose()
    } catch (err) { setError(err instanceof Error ? err.message : 'We could not save the client. Please try again.') }
    finally { setSaving(false) }
  }

  return <Dialog open onClose={close} width="lg" scroll="inside"
    title={creating ? 'New client' : 'Edit client'}
    footer={<><Button variant="ghost" disabled={saving} onClick={close}>Cancel</Button><Button variant="primary" type="submit" form="client-form" disabled={saving}>{saving ? 'Saving…' : 'Save client'}</Button></>}>
    <DialogMark />
    {error && <p role="alert" className="client-form-error">{error}</p>}
    <form id="client-form" className="client-form" onSubmit={(event) => { event.preventDefault(); void save() }}>
      <fieldset disabled={saving} className="client-form-fields">
        <FormSection icon={<UserRound size={18} />} title="Contact person">
          <Field label={creating ? 'Contact person' : 'Full name'} required><Input required maxLength={200} value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="Khalid Rahman" /></Field>
          <Field label="Designation"><Input maxLength={120} value={form.designation} onChange={(e) => set('designation', e.target.value)} placeholder="HR Manager" /></Field>
          <Field label="Email"><Input type="email" maxLength={255} value={form.email} onChange={(e) => set('email', e.target.value)} placeholder="name@company.com" /></Field>
          <Field label="Phone"><Input type="tel" maxLength={40} value={form.phone} onChange={(e) => set('phone', e.target.value)} placeholder="+971 50 123 4567" /></Field>
          {!creating && <Field label="Country"><CountryInput value={form.country} onChange={(value) => set('country', value)} options={countries} /></Field>}
          {!creating && <Field label="City"><Input maxLength={100} value={form.city} onChange={(e) => set('city', e.target.value)} placeholder="Dubai" /></Field>}
        </FormSection>
        {creating && <FormSection icon={<Building2 size={18} />} title="Company">
          <Field label="Company name" required className="sm:col-span-2"><Input required maxLength={200} value={employer.name} onChange={(e) => setCompany('name', e.target.value)} placeholder="Gulf Build Contracting LLC" /></Field>
          <Field label="Website"><Input type="text" inputMode="url" autoCapitalize="none" autoCorrect="off" spellCheck={false} maxLength={WEBSITE_MAX} value={employer.website} onChange={(e) => setCompany('website', e.target.value)} placeholder="www.gulfbuild.com" /></Field>
          <Field label="Country"><CountryInput value={form.country} onChange={(value) => set('country', value)} options={countries} /></Field>
          <Field label="City"><Input maxLength={100} value={form.city} onChange={(e) => set('city', e.target.value)} placeholder="Dubai" /></Field>
          <Field label="Address" className="sm:col-span-2"><Textarea rows={2} maxLength={2000} value={employer.address} onChange={(e) => setCompany('address', e.target.value)} placeholder="Building, street, area" /></Field>
        </FormSection>}
        <FormSection icon={<UserCheck size={18} />} title="Allocate staff" cols={1}>
          <div className="clients-simple"><StaffPicker users={staffUsers} value={staffIds} onChange={setStaffIds} disabled={saving} /></div>
        </FormSection>
      </fieldset>
    </form>
  </Dialog>
}
