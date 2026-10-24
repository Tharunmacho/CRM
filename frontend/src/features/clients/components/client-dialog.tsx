import { useMemo, useState } from 'react'
import { Building2, FileText, UserRound } from 'lucide-react'
import { api } from '@/shared/api/client'
import type { Client, Company } from '@/shared/model/types'
import { useStore } from '@/shared/state/crm-store'
import { Button, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { FileDrop } from '@/shared/ui/file-drop'
import { refreshMasterData } from '@/features/master-data/components/record-audit'
import { DialogMark } from '@/features/master-data/components/dialog-mark'
import { COUNTRY_SUGGESTIONS, Flag, FormSection, countryCode } from '@/features/clients/components/parts'

/** Country names already on record, then the destinations we ship flags for that no record already covers. */
export function useCountryOptions() {
  const store = useStore()
  return useMemo(() => {
    const known = [...new Set([...store.clients.map((c) => c.country?.trim()), ...store.companies.map((c) => c.country?.trim())].filter(Boolean) as string[])]
    const covered = new Set(known.map(countryCode))
    return [...known, ...COUNTRY_SUGGESTIONS.filter((name) => !covered.has(countryCode(name)))]
  }, [store.clients, store.companies])
}

/** A country field that suggests names as you type and shows the flag it recognises. */
export function CountryInput({ value, onChange, listId, options }: { value: string; onChange: (value: string) => void; listId: string; options: string[] }) {
  return (
    <div className="client-form-flag">
      <Flag country={value} />
      <Input className="min-w-0 flex-1" maxLength={100} list={listId} value={value} onChange={(event) => onChange(event.target.value)} autoComplete="off" />
      <datalist id={listId}>{options.map((name) => <option key={name} value={name} />)}</datalist>
    </div>
  )
}

/**
 * A client is the employer (PRD: company name, country, city, industry, contact person,
 * contact email, phone/WhatsApp, address, agreement, notes, status). Creating one saves the
 * company and its contact person together; editing changes the contact, while each employer
 * keeps its own details and agreement on its own record.
 */
export function ClientDialog({ client, onClose }: { client?: Client; onClose: () => void }) {
  const store = useStore()
  const countries = useCountryOptions()
  const [form, setForm] = useState({
    name: client?.name ?? '', email: client?.email ?? '', phone: client?.phone ?? '',
    whatsapp: client?.whatsapp ?? '', country: client?.country ?? '', city: client?.city ?? '',
    notes: client?.notes ?? '', status: client?.status ?? 'active',
  })
  const [employer, setEmployer] = useState({ name: '', industry: '', address: '' })
  const [agreement, setAgreement] = useState<File | null>(null)
  // Set once the records exist, so a failed agreement upload is retried without a duplicate.
  const [saved, setSaved] = useState<{ companyId: string } | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const set = (key: string, value: string) => setForm((previous) => ({ ...previous, [key]: value }))
  const setCompany = (key: string, value: string) => setEmployer((previous) => ({ ...previous, [key]: value }))
  const close = () => { if (!saving) onClose() }
  const creating = !client

  const save = async () => {
    if (saving) return
    setSaving(true); setError('')
    let companyId = saved?.companyId
    try {
      if (!creating) {
        await api.put('/clients/' + client.id, form)
        await refreshMasterData(store, 'Client saved')
        onClose()
        return
      }
      if (!companyId) {
        const created = await api.post<Client & { company: Company }>('/clients', { ...form, company: { name: employer.name, industry: employer.industry, address: employer.address, country: form.country, city: form.city } })
        companyId = created.company.id
        setSaved({ companyId })
      }
      if (agreement) {
        try {
          await api.upload('company', companyId, 'agreement', agreement)
          setAgreement(null)
        } catch (err) {
          await refreshMasterData(store, 'Client saved')
          setError('The client and company are saved, but the agreement did not upload. Press Save client to try again, or close and upload it later from the company. ' + (err instanceof Error ? err.message : ''))
          return
        }
      }
      await refreshMasterData(store, 'Client saved')
      onClose()
    } catch (err) { setError(err instanceof Error ? err.message : 'We could not save the client. Please try again.') }
    finally { setSaving(false) }
  }

  return <Dialog open onClose={close} width="lg" scroll="inside"
    title={creating ? (saved ? 'Finish uploading the agreement' : 'New client') : 'Edit client'}
    description={creating ? 'The company and the person you deal with are saved together.' : 'Edit the contact person here. Company details are edited on each company.'}
    footer={<><Button variant="ghost" disabled={saving} onClick={close}>{saved && error ? 'Close and finish later' : 'Cancel'}</Button><Button variant="primary" type="submit" form="client-form" disabled={saving}>{saving ? 'Saving…' : 'Save client'}</Button></>}>
    <DialogMark />
    {error && <p role="alert" className="client-form-error">{error}</p>}
    <form id="client-form" className="client-form" onSubmit={(event) => { event.preventDefault(); void save() }}>
      <fieldset disabled={saving || !!saved} className="client-form-fields">
        {creating && <FormSection icon={<Building2 size={18} />} title="Company" hint="The company that will give you orders">
          <Field label="Company name" required className="sm:col-span-2"><Input required maxLength={200} value={employer.name} onChange={(e) => setCompany('name', e.target.value)} placeholder="Gulf Build Contracting LLC" /></Field>
          <Field label="Industry"><Input maxLength={120} value={employer.industry} onChange={(e) => setCompany('industry', e.target.value)} placeholder="Construction" /></Field>
          <Field label="Status" hint="Inactive clients cannot get new orders."><Select value={form.status} onChange={(e) => set('status', e.target.value)}><option value="active">Active</option><option value="inactive">Inactive</option></Select></Field>
          <Field label="Country"><CountryInput value={form.country} onChange={(value) => set('country', value)} listId="client-countries" options={countries} /></Field>
          <Field label="City"><Input maxLength={100} value={form.city} onChange={(e) => set('city', e.target.value)} /></Field>
          <Field label="Address" className="sm:col-span-2"><Textarea rows={2} maxLength={2000} value={employer.address} onChange={(e) => setCompany('address', e.target.value)} placeholder="Building, street, area" /></Field>
        </FormSection>}
        <FormSection icon={<UserRound size={18} />} title="Contact person" hint={creating ? 'The person you talk to' : undefined}>
          <Field label={creating ? 'Contact person' : 'Full name'} required className="sm:col-span-2"><Input required maxLength={200} value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="Khalid Rahman" /></Field>
          <Field label="Email"><Input type="email" maxLength={255} value={form.email} onChange={(e) => set('email', e.target.value)} /></Field>
          <Field label="Phone"><Input type="tel" maxLength={40} value={form.phone} onChange={(e) => set('phone', e.target.value)} /></Field>
          <Field label="WhatsApp"><Input type="tel" maxLength={40} value={form.whatsapp} onChange={(e) => set('whatsapp', e.target.value)} /></Field>
          {!creating && <Field label="Status" hint="Inactive clients cannot get new companies or orders."><Select value={form.status} onChange={(e) => set('status', e.target.value)}><option value="active">Active</option><option value="inactive">Inactive</option></Select></Field>}
          {!creating && <Field label="Country"><CountryInput value={form.country} onChange={(value) => set('country', value)} listId="client-countries" options={countries} /></Field>}
          {!creating && <Field label="City"><Input maxLength={100} value={form.city} onChange={(e) => set('city', e.target.value)} /></Field>}
        </FormSection>
      </fieldset>
      {creating && <FormSection icon={<FileText size={18} />} title="Agreement" hint="Optional. If you upload a new one later, the old one is kept." cols={1}>
        <FileDrop value={agreement ? { name: agreement.name, sizeKb: Math.max(1, Math.round(agreement.size / 1024)) } : null}
          disabled={saving} uploaderName={(id) => store.user(id)?.name ?? 'Team member'}
          help="Choose the signed agreement. It uploads when you press Save client."
          onFile={(file) => setAgreement(file)} />
        {agreement && !saving && <div><Button size="sm" variant="ghost" onClick={() => setAgreement(null)}>Remove this file</Button></div>}
      </FormSection>}
      <fieldset disabled={saving || !!saved} className="client-form-fields">
        <Field label="Notes"><Textarea rows={3} maxLength={20000} value={form.notes} onChange={(e) => set('notes', e.target.value)} /></Field>
      </fieldset>
    </form>
  </Dialog>
}
