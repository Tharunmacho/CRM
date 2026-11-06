import { useMemo, useState } from 'react'
import { BookUser, Building2, FileText, UserRound } from 'lucide-react'
import { api } from '@/shared/api/client'
import type { Client, Company } from '@/shared/model/types'
import { useStore } from '@/shared/state/crm-store'
import { Button, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { SuggestInput } from '@/shared/ui/suggest-input'
import { FileDrop } from '@/shared/ui/file-drop'
import { refreshMasterData } from '@/features/master-data/components/record-audit'
import { DialogMark } from '@/features/master-data/components/dialog-mark'
import { Flag, FormSection } from '@/features/clients/components/parts'
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
 * A client is the employer (PRD: company name, country, city, industry, contact person,
 * contact email, phone/WhatsApp, address, agreement, notes, status). This is the one place a
 * client is added: creating saves the contact person, the company, its optional own contact and
 * the agreement together. Editing changes the contact person; each company keeps its own details
 * and agreement on its own record (client page), where more companies can be added later.
 */
export function ClientDialog({ client, onClose }: { client?: Client; onClose: () => void }) {
  const store = useStore()
  const countries = useCountryOptions()
  // Industries already saved on companies, most used first. Any other name can still be typed.
  const industries = useMemo(() => frequentValues(store.companies.map((c) => c.industry)), [store.companies])
  const [form, setForm] = useState({
    name: client?.name ?? '', designation: client?.designation ?? '', email: client?.email ?? '', phone: client?.phone ?? '',
    whatsapp: client?.whatsapp ?? '', country: client?.country ?? '', city: client?.city ?? '',
    notes: client?.notes ?? '', status: client?.status ?? 'active',
  })
  // Company-only fields. The contact ones are optional: left blank, the contact person's details apply.
  const [employer, setEmployer] = useState({ name: '', industry: '', website: '', address: '', contactPerson: '', contactEmail: '', phone: '', whatsapp: '' })
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
    const web = creating ? checkWebsite(employer.website) : { value: '', error: '' }
    if (web.error) { setError('Website: ' + web.error); return }
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
        const created = await api.post<Client & { company: Company }>('/clients', { ...form, company: { ...employer, website: web.value, country: form.country, city: form.city } })
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
    description={creating ? 'Add the client and its company in one go. You can add more companies later from the client page.' : 'Edit the contact person here. Company details are edited on each company.'}
    footer={<><Button variant="ghost" disabled={saving} onClick={close}>{saved && error ? 'Close and finish later' : 'Cancel'}</Button><Button variant="primary" type="submit" form="client-form" disabled={saving}>{saving ? 'Saving…' : 'Save client'}</Button></>}>
    <DialogMark />
    {error && <p role="alert" className="client-form-error">{error}</p>}
    <form id="client-form" className="client-form" onSubmit={(event) => { event.preventDefault(); void save() }}>
      <fieldset disabled={saving || !!saved} className="client-form-fields">
        <FormSection icon={<UserRound size={18} />} title={creating ? 'Client contact person' : 'Contact person'} hint={creating ? 'The person you deal with' : undefined}>
          <Field label={creating ? 'Contact person' : 'Full name'} required><Input required maxLength={200} value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="Khalid Rahman" /></Field>
          <Field label="Designation" hint="Their job title"><Input maxLength={120} value={form.designation} onChange={(e) => set('designation', e.target.value)} placeholder="HR Manager" /></Field>
          <Field label="Email"><Input type="email" maxLength={255} value={form.email} onChange={(e) => set('email', e.target.value)} placeholder="name@company.com" /></Field>
          <Field label="Phone"><Input type="tel" maxLength={40} value={form.phone} onChange={(e) => set('phone', e.target.value)} placeholder="+971 50 123 4567" /></Field>
          <Field label="WhatsApp"><Input type="tel" maxLength={40} value={form.whatsapp} onChange={(e) => set('whatsapp', e.target.value)} placeholder="+971 50 123 4567" /></Field>
          <Field label="Status" hint={creating ? 'Inactive clients cannot get new orders.' : 'Inactive clients cannot get new companies or orders.'}><Select value={form.status} onChange={(e) => set('status', e.target.value)}><option value="active">Active</option><option value="inactive">Inactive</option></Select></Field>
          {!creating && <Field label="Country"><CountryInput value={form.country} onChange={(value) => set('country', value)} options={countries} /></Field>}
          {!creating && <Field label="City"><Input maxLength={100} value={form.city} onChange={(e) => set('city', e.target.value)} placeholder="Dubai" /></Field>}
        </FormSection>
        {creating && <FormSection icon={<Building2 size={18} />} title="Company" hint="The company that will give you orders">
          <Field label="Company name" required className="sm:col-span-2"><Input required maxLength={200} value={employer.name} onChange={(e) => setCompany('name', e.target.value)} placeholder="Gulf Build Contracting LLC" /></Field>
          <Field label="Industry"><SuggestInput maxLength={120} value={employer.industry} onChange={(value) => setCompany('industry', value)} options={industries} placeholder="Construction" /></Field>
          <Field label="Country"><CountryInput value={form.country} onChange={(value) => set('country', value)} options={countries} /></Field>
          <Field label="City"><Input maxLength={100} value={form.city} onChange={(e) => set('city', e.target.value)} placeholder="Dubai" /></Field>
          <Field label="Website" hint="Optional. For example www.gulfbuild.com"><Input type="text" inputMode="url" autoCapitalize="none" autoCorrect="off" spellCheck={false} maxLength={WEBSITE_MAX} value={employer.website} onChange={(e) => setCompany('website', e.target.value)} placeholder="www.gulfbuild.com" /></Field>
          <Field label="Address" className="sm:col-span-2"><Textarea rows={2} maxLength={2000} value={employer.address} onChange={(e) => setCompany('address', e.target.value)} placeholder="Building, street, area" /></Field>
        </FormSection>}
        {creating && <FormSection icon={<BookUser size={18} />} title="Company contact" hint="Optional. Leave blank to use the contact person's details above.">
          <Field label="Contact person"><Input maxLength={160} value={employer.contactPerson} onChange={(e) => setCompany('contactPerson', e.target.value)} placeholder={form.name || 'Same as the contact person'} /></Field>
          <Field label="Contact email"><Input type="email" maxLength={255} value={employer.contactEmail} onChange={(e) => setCompany('contactEmail', e.target.value)} placeholder={form.email || 'Same as the contact person'} /></Field>
          <Field label="Phone"><Input type="tel" maxLength={40} value={employer.phone} onChange={(e) => setCompany('phone', e.target.value)} placeholder={form.phone || 'Same as the contact person'} /></Field>
          <Field label="WhatsApp"><Input type="tel" maxLength={40} value={employer.whatsapp} onChange={(e) => setCompany('whatsapp', e.target.value)} placeholder={form.whatsapp || 'Same as the contact person'} /></Field>
        </FormSection>}
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
