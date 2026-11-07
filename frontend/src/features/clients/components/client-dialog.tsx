import { useMemo, useState } from 'react'
import { Building2, UserCheck, UserRound } from 'lucide-react'
import { api } from '@/shared/api/client'
import type { Client, Company } from '@/shared/model/types'
import { useStore } from '@/shared/state/crm-store'
import { Button, Field, Input } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { SuggestInput } from '@/shared/ui/suggest-input'
import { refreshMasterData } from '@/features/master-data/components/record-audit'
import { DialogMark } from '@/features/master-data/components/dialog-mark'
import { Flag } from '@/features/clients/components/parts'
import { Section, RecruiterPicker, allocatableUsers } from '@/features/clients/components/party-fields'
import '@/styles/clients-simple.css'
import { frequentValues } from '@/shared/lib/suggestions'
import { COUNTRY_NAMES } from '@/shared/lib/country-flag'
import { WEBSITE_MAX, checkWebsite } from '@/shared/lib/website'

/** Countries already saved on clients, companies and orders come first (most used first), then every other country A to Z. */
export function useCountryOptions() {
  const store = useStore()
  return useMemo(() => {
    const used = frequentValues([...store.clients.map((c) => c.country), ...store.companies.map((c) => c.country), ...store.orders.map((o) => o.country)])
    const seen = new Set(used.map((name) => name.toLowerCase()))
    return [...used, ...COUNTRY_NAMES.filter((name) => !seen.has(name.toLowerCase()))]
  }, [store.clients, store.companies, store.orders])
}

/** A country field that suggests names as you type and shows the flag it recognises. */
export function CountryInput({ value, onChange, options }: { value: string; onChange: (value: string) => void; options: string[] }) {
  const canonical = () => {
    const match = options.find((name) => name.toLowerCase() === value.trim().toLowerCase())
    if (match && match !== value) onChange(match)
  }
  return (
    <div className="client-form-flag" onBlur={canonical}>
      <Flag country={value} />
      <SuggestInput wrapperClassName="min-w-0 flex-1" maxLength={100} value={value} onChange={onChange} options={options} aria-label="Country" placeholder="Start typing a country" />
    </div>
  )
}

/**
 * A client is the contact person plus the company that gives the orders. Creating saves both together (company name, country and
 * website, then the contact person) and the CRM recruiters who look after the client. Editing changes the same things, and
 * the company's name, website and country are saved on its record too (all its other details are sent back unchanged).
 */
export function ClientDialog({ client, onClose }: { client?: Client; onClose: () => void }) {
  const store = useStore()
  const countries = useCountryOptions()
  const [form, setForm] = useState({
    name: client?.name ?? '', designation: client?.designation ?? '', email: client?.email ?? '', phone: client?.phone ?? '',
    country: client?.country ?? '',
  })
  // Company fields typed when a client is created.
  // The company already on file for the client being edited: its name, website and country are changed here too.
  const company = client ? store.companies.find((item) => item.clientId === client.id) : undefined
  const [employer, setEmployer] = useState({ name: company?.name ?? '', website: company?.website ?? '' })
  // CRM recruiters who look after this client. Only people who can still be allocated are kept: the server refuses inactive users and admins.
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
    const web = creating || company ? checkWebsite(employer.website) : { value: '', error: '' }
    if (web.error) { setError('Website: ' + web.error); return }
    setSaving(true); setError('')
    try {
      if (client) {
        // The API replaces the whole record, so what this form no longer shows is sent back as it was saved.
        await api.put('/clients/' + client.id, {
          ...form, city: client.city ?? '', whatsapp: client.whatsapp ?? '', status: client.status, notes: client.notes ?? '', staffContacts: client.staffContacts ?? [], staffIds,
        })
        // The company keeps every other detail (agreement, address, notes ...) exactly as saved.
        if (company) {
          await api.put('/companies/' + company.id, {
            clientId: company.clientId, name: employer.name.trim() || company.name, country: form.country, city: company.city ?? '', industry: company.industry ?? '',
            contactPerson: company.contactPerson ?? '', contactEmail: company.contactEmail ?? '', website: web.value, phone: company.phone ?? '', whatsapp: company.whatsapp ?? '',
            address: company.address ?? '', notes: company.notes ?? '', status: company.status ?? 'active',
          })
        }
      } else {
        await api.post<Client & { company: Company }>('/clients', { ...form, staffIds, company: { name: employer.name, website: web.value, address: '', state: '', country: form.country, city: '' } })
      }
      await refreshMasterData(store, 'Client saved')
      onClose()
    } catch (err) { setError(err instanceof Error ? err.message : 'We could not save the client. Please try again.') }
    finally { setSaving(false) }
  }

  return <Dialog open onClose={close} width="lg" scroll="inside"
    title={creating ? 'New client' : 'Edit client'}
    icon={<span className="cs-dlg-chip"><Building2 size={22} strokeWidth={1.9} /></span>}
    footer={<><Button variant="ghost" disabled={saving} onClick={close}>Cancel</Button><Button variant="primary" type="submit" form="client-form" disabled={saving}>{saving ? 'Saving…' : 'Save'}</Button></>}>
    <DialogMark />
    {error && <p role="alert" className="client-form-error">{error}</p>}
    <form id="client-form" className="client-form clients-simple" onSubmit={(event) => { event.preventDefault(); void save() }}>
      <fieldset disabled={saving} className="client-form-fields cs-sections">
        <Section icon={<Building2 size={17} />} title="Company">
          <div className="cs-grid6">
            {(creating || company) && <Field label="Company name" required className="span-6"><Input required maxLength={200} value={employer.name} onChange={(e) => setCompany('name', e.target.value)} placeholder="Gulf Build Contracting LLC" autoComplete="off" /></Field>}
            <Field label="Country" className="span-3"><CountryInput value={form.country} onChange={(value) => set('country', value)} options={countries} /></Field>
            {(creating || company) && <Field label="Website" className="span-3"><Input type="text" inputMode="url" autoCapitalize="none" autoCorrect="off" spellCheck={false} maxLength={WEBSITE_MAX} value={employer.website} onChange={(e) => setCompany('website', e.target.value)} placeholder="www.gulfbuild.com" /></Field>}
          </div>
        </Section>
        <Section icon={<UserRound size={17} />} title="Contact person">
          <div className="cs-grid6">
            <Field label="Contact person" required className="span-3"><Input required maxLength={200} value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="Khalid Rahman" autoComplete="off" /></Field>
            <Field label="Designation" className="span-3"><Input maxLength={120} value={form.designation} onChange={(e) => set('designation', e.target.value)} placeholder="HR Manager" autoComplete="off" /></Field>
            <Field label="Email" className="span-3"><Input type="email" maxLength={255} value={form.email} onChange={(e) => set('email', e.target.value)} placeholder="name@company.com" autoComplete="off" /></Field>
            <Field label="Phone" className="span-3"><Input type="tel" maxLength={40} value={form.phone} onChange={(e) => set('phone', e.target.value)} placeholder="+971 50 123 4567" autoComplete="off" /></Field>
          </div>
        </Section>
        <Section icon={<UserCheck size={17} />} title="Assign recruiter">
          <RecruiterPicker users={staffUsers} value={staffIds} onChange={setStaffIds} disabled={saving} />
        </Section>
      </fieldset>
    </form>
  </Dialog>
}
