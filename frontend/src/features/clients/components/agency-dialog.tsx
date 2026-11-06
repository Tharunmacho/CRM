import { useState } from 'react'
import { Users } from 'lucide-react'
import { ApiError } from '@/shared/api/client'
import type { ReferenceAgency } from '@/shared/model/types'
import { useStore } from '@/shared/state/crm-store'
import { Button } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { DialogMark } from '@/features/master-data/components/dialog-mark'
import { PartyFieldsSection, StaffDetailsSection } from '@/features/clients/components/party-fields'
import { agencyApi } from '@/features/clients/agency-api'
import { buildAgencyPayload, emptyParty, fieldErrorsFromApi, staffRowsOf, validateParty, type FieldErrors, type PartyFields, type StaffRow } from '@/features/clients/client-form'
import '@/styles/clients-simple.css'

/** Add or edit a reference agency: the same details and staff details as a client, without allocated CRM staff. */
export function AgencyDialog({ agency, onClose, onSaved }: { agency?: ReferenceAgency; onClose: () => void; onSaved: () => void }) {
  const store = useStore()
  const creating = !agency
  const [values, setValues] = useState<PartyFields>(() => agency
    ? { name: agency.name, agencyName: agency.agencyName, phone: agency.phone ?? '', email: agency.email ?? '', address: agency.address ?? '', state: agency.state ?? '' }
    : emptyParty())
  const [rows, setRows] = useState<StaffRow[]>(() => staffRowsOf(agency?.staffContacts))
  const [errors, setErrors] = useState<FieldErrors>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const set = (key: keyof PartyFields, value: string) => { setValues((previous) => ({ ...previous, [key]: value })); if (errors[key]) setErrors(({ [key]: _gone, ...rest }) => rest) }
  const close = () => { if (!saving) onClose() }

  const save = async () => {
    if (saving) return
    const problems = validateParty(values, rows)
    setErrors(problems)
    if (Object.keys(problems).length) { setError('Please fix the highlighted fields.'); return }
    setSaving(true); setError('')
    try {
      const payload = buildAgencyPayload(values, rows)
      if (agency) await agencyApi.update(agency.id, payload)
      else await agencyApi.create(payload)
      store.notify('Agency saved')
      onSaved()
      onClose()
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        const mapped = fieldErrorsFromApi(err.detail)
        setErrors(mapped.fields)
        setError(mapped.general || (Object.keys(mapped.fields).length ? 'Please fix the highlighted fields.' : err.message))
      } else setError(err instanceof Error ? err.message : 'We could not save the agency. Please try again.')
    } finally { setSaving(false) }
  }

  return <Dialog open onClose={close} width="lg" scroll="inside"
    title={creating ? 'New agency' : 'Edit agency'}
    description="An agency that refers candidates to you."
    icon={<span className="cs-dlg-chip"><Users size={22} strokeWidth={1.9} /></span>}
    footer={<><Button variant="ghost" disabled={saving} onClick={close}>Cancel</Button><Button variant="primary" type="submit" form="agency-form" disabled={saving}>{saving ? 'Saving…' : 'Save agency'}</Button></>}>
    <DialogMark />
    {error && <p role="alert" className="client-form-error">{error}</p>}
    <form id="agency-form" noValidate className="client-form clients-simple" onSubmit={(event) => { event.preventDefault(); void save() }}>
      <fieldset disabled={saving} className="client-form-fields">
        <PartyFieldsSection values={values} onChange={set} errors={errors} />
        <StaffDetailsSection rows={rows} onChange={setRows} errors={errors} />
      </fieldset>
    </form>
  </Dialog>
}
