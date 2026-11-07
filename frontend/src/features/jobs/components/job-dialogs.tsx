import { useMemo, useRef, useState } from 'react'
import { Briefcase, CalendarDays, ListChecks, MapPin } from 'lucide-react'
import { api, ApiError } from '@/shared/api/client'
import type { Designation, Order } from '@/shared/model/types'
import { useStore } from '@/shared/state/crm-store'
import { currencyChoices, currencyLabel } from '@/shared/lib/agency'
import { Button, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { COUNTRY_NAMES, countryCode } from '@/shared/lib/country-flag'
import { DatePicker } from '@/shared/ui/date-picker'
import { Dialog } from '@/shared/ui/overlay'
import { refreshMasterData } from '@/features/master-data/components/record-audit'
import { DialogMark } from '@/features/master-data/components/dialog-mark'
import { Flag, FormSection } from '@/features/clients/components/parts'
import { useCountryOptions } from '@/features/clients/components/client-dialog'
import { ComboInput, TagInput } from '@/features/jobs/components/search-select'
import { canonicalCountry, countryOptions } from '@/features/jobs/country-options'
import { ClientPicker } from '@/features/jobs/components/client-picker'
import {
  buildClientOptions, buildJobPayload, decideTitle, emptyJobForm, jobErrorsFromApi, nextJobForm, planDesignation, requirementsOf, skillSuggestions, titleKey, validateJob,
  type JobFieldErrors, type JobForm,
} from '@/features/jobs/job-form'
import '@/styles/jobs.css'
import '@/styles/jobs-simple.css'

/** Removing a candidate from a job closes their application there; the candidate is not deleted. */
export function UnmapCandidateDialog({ name, saving, error, onClose, onConfirm }: { name: string; saving: boolean; error: string; onClose: () => void; onConfirm: () => void }) {
  return <Dialog open onClose={() => { if (!saving) onClose() }} title="Remove this candidate from the job?"
    description="This closes their application on this job and cancels its open tasks. The candidate goes back to the Data Bank and can be matched to another job."
    footer={<><Button variant="ghost" disabled={saving} onClick={onClose}>Keep on job</Button><Button variant="danger" disabled={saving} onClick={onConfirm}>{saving ? 'Removing…' : 'Remove from job'}</Button></>}>
    <DialogMark />
    <p className="text-sm text-ink-600">Stages {name} has already done stay on record. If you match them again later, they start again at Stage 1.</p>
    {error && <p role="alert" className="mt-3 text-sm text-stamp-700">{error}</p>}
  </Dialog>
}

/**
 * New job / Edit job. A job is placed for a client, chosen from the clients that already exist (one entry per company), and has a
 * job title chosen from the saved titles or typed: a title that is not saved yet is created first, never twice. `companyId` fixes
 * the client (opened from the client's own page); `designation` pre-fills the title. The server still calls a job an order.
 */
export function OrderDialog({ designation, order, companyId, onClose, onSaved }: { designation?: Designation; order?: Order; companyId?: string; onClose: () => void; onSaved?: (id: string) => void }) {
  const store = useStore()
  const used = useCountryOptions()
  const countries = useMemo(() => countryOptions(used, COUNTRY_NAMES, countryCode), [used])
  const properCountry = (typed: string) => canonicalCountry(typed, COUNTRY_NAMES, countryCode)
  const creating = !order
  const [form, setForm] = useState<JobForm>(() => order ? {
    companyId: order.companyId, title: store.designation(order.designationId)?.title ?? '', ...requirementsOf(store.designation(order.designationId)), openings: String(order.openings), country: properCountry(order.country ?? ''), city: order.city ?? '',
    salary: order.salary === undefined || order.salary === null ? '' : String(order.salary), currency: order.currency, deadline: order.deadline ?? '',
    benefits: order.benefits.join(', '), contractPeriod: order.contractPeriod ?? '', interviewDate: order.interviewDate ?? '', status: order.status,
  } : { ...emptyJobForm(companyId ?? '', store.settings.defaultOrderCurrency), title: designation?.title ?? '', ...requirementsOf(designation) })
  const [errors, setErrors] = useState<JobFieldErrors>({})
  const [general, setGeneral] = useState('')
  const [saving, setSaving] = useState(false)
  // Titles this dialog already added (title key -> id), so a second try after a failed save never adds a copy.
  const created = useRef(new Map<string, string>())
  const titleBox = useRef<HTMLInputElement>(null)
  // The saved title whose requirements are showing, so choosing another title swaps them and typing a new one clears them.
  const shownFor = useRef(order?.designationId ?? designation?.id ?? '')

  const history = order ? store.applications.filter((application) => application.orderId === order.id) : []
  const counts = { deployed: history.filter((application) => application.status === 'deployed').length, active: history.filter((application) => application.status === 'active').length }
  const locked = history.length > 0
  const clientFixed = locked || (!!companyId && creating)
  const options = useMemo(
    () => buildClientOptions({ clients: store.clients, companies: store.companies, orders: store.orders, keepCompanyId: order?.companyId ?? companyId }),
    [store.clients, store.companies, store.orders, order?.companyId, companyId],
  )
  const titles = useMemo(() => store.designations.map((item) => item.title), [store.designations])
  const skillChoices = useMemo(() => skillSuggestions(store.designations), [store.designations])
  const decision = decideTitle(form.title, store.designations, created.current)

  const set = (key: keyof JobForm, value: string) => {
    setForm((previous) => ({ ...previous, [key]: value }))
    if (errors[key]) setErrors((previous) => { const next = { ...previous }; delete next[key]; return next })
  }
  const close = () => { if (!saving) onClose() }
  const setTitle = (value: string) => {
    const pick = decideTitle(value, store.designations, created.current)
    const saved = pick.kind === 'existing' ? store.designations.find((item) => item.id === pick.id) : undefined
    setForm((previous) => {
      const next = { ...previous, title: value }
      if (saved && saved.id !== shownFor.current) Object.assign(next, requirementsOf(saved))
      else if (!saved && shownFor.current) Object.assign(next, requirementsOf(null))
      return next
    })
    shownFor.current = saved?.id ?? ''
    if (errors.title) setErrors((previous) => { const rest = { ...previous }; delete rest.title; return rest })
  }

  const save = async (another: boolean) => {
    if (saving) return
    const typed = { ...form, country: properCountry(form.country) }
    const found = validateJob(typed, counts)
    setErrors(found); setGeneral('')
    if (Object.keys(found).length > 0) return
    if (typed.country !== form.country) setForm(typed)
    setSaving(true)
    let designationId = ''
    let stage: 'title' | 'job' = 'title'
    try {
      // The job title comes first: a new title is created with the requirements typed, a saved title whose requirements were changed is updated.
      const plan = planDesignation(typed, store.designations, created.current)
      if (plan.kind === 'use') designationId = plan.id
      else if (plan.kind === 'create') {
        const record = await api.post<Designation>('/designations', plan.body)
        created.current.set(titleKey(plan.body.title), record.id)
        designationId = record.id
      } else if (plan.kind === 'update') {
        await api.put<Designation>('/designations/' + plan.id, plan.body)
        designationId = plan.id
      }
      if (!designationId) { setErrors({ title: 'Enter or choose the job title.' }); return }
      stage = 'job'
      const payload = buildJobPayload(typed, designationId)
      const record = order ? await api.put<Order>('/orders/' + order.id, payload) : await api.post<Order>('/orders', payload)
      await refreshMasterData(store, 'Job saved')
      if (store.isCurrentSession()) onSaved?.(record.id)
      if (another && creating) {
        // Same client, a fresh job: only the job's own boxes start over.
        setForm(nextJobForm(typed)); setErrors({}); shownFor.current = ''
        window.setTimeout(() => titleBox.current?.focus(), 0)
      } else onClose()
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : undefined
      const message = err instanceof Error ? err.message : ''
      const mapped = jobErrorsFromApi(detail, message)
      // A problem with the title step that belongs to no box is shown on the title.
      if (stage === 'title' && Object.keys(mapped.fields).length === 0) setErrors({ title: mapped.general || 'We could not save this job title. Please try again.' })
      else {
        setErrors(mapped.fields)
        setGeneral(mapped.general || (Object.keys(mapped.fields).length > 0 ? '' : 'We could not save the job. Please try again.'))
      }
    } finally { setSaving(false) }
  }

  const statusBlocked = (key: string) => key === 'filled' ? counts.deployed < Number(form.openings) : key === 'closed' ? counts.active > 0 : false

  return <Dialog open onClose={close} width="lg" scroll="inside" title={order ? `Edit job · ${order.ref}` : 'New job'}
    footer={<>
      <Button variant="ghost" disabled={saving} onClick={close}>Cancel</Button>
      {creating && <Button disabled={saving} onClick={() => void save(true)}>Save and add another job</Button>}
      <Button variant="primary" type="submit" form="job-form" disabled={saving}>{saving ? 'Saving…' : 'Save job'}</Button>
    </>}>
    <DialogMark />
    {general && <p role="alert" className="client-form-error">{general}</p>}
    <form id="job-form" className="form-stack jv-form jobs-simple" noValidate onSubmit={(event) => { event.preventDefault(); void save(false) }}>
      <fieldset disabled={saving} className="form-stack js-fields">
        <FormSection icon={<Briefcase size={18} />} title="Job">
          <div className="ui-field sm:col-span-2">
            <span className="ui-field-label">Client<span className="ui-field-req" aria-hidden="true">*</span></span>
            <ClientPicker options={options} value={form.companyId} locked={clientFixed} disabled={saving} invalid={!!errors.companyId} emptyText="No active clients yet. Add a client first."
              onChange={(option) => set('companyId', option.companyId)} />
            {errors.companyId ? <span className="ui-field-error" role="alert">{errors.companyId}</span> : locked ? <span className="ui-field-hint">The client cannot change once candidates are matched.</span> : null}
          </div>
          <Field label="Job title" required className="sm:col-span-2" error={errors.title}
            hint={locked ? 'The job title cannot change once candidates are matched.' : decision.kind === 'new' ? `“${decision.title}” is new and will be added to your job titles.` : undefined}>
            <ComboInput inputRef={titleBox} maxLength={160} value={form.title} onChange={setTitle} options={titles} disabled={locked} invalid={!!errors.title}
              placeholder="Choose a job title or type a new one" ariaLabel="Job title" noMatchNote={(typed) => `“${typed}” will be added as a new job title.`} />
          </Field>
          <Field label="Openings" required error={errors.openings}>
            <Input type="number" inputMode="numeric" min={Math.max(1, counts.deployed)} max={100000} step={1} value={form.openings} onChange={(e) => set('openings', e.target.value)} aria-invalid={!!errors.openings} />
          </Field>
          <Field label="Deadline" error={errors.deadline}><DatePicker value={form.deadline} onChange={(value) => set('deadline', value)} /></Field>
          {!creating && <Field label="Status" error={errors.status}>
            <Select value={form.status} onChange={(e) => set('status', e.target.value)}>
              <option value="open">Open</option><option value="on_hold">On hold</option>
              <option value="filled" disabled={statusBlocked('filled')}>Filled</option><option value="closed" disabled={statusBlocked('closed')}>Closed</option>
            </Select>
          </Field>}
        </FormSection>

        <FormSection icon={<ListChecks size={18} />} title="Requirements" hint="Applies to every job with this title.">
          <div className="ui-field sm:col-span-2">
            <span className="ui-field-label">Required skills</span>
            <TagInput value={form.skills} onChange={(skills) => setForm((previous) => ({ ...previous, skills }))} suggestions={skillChoices} ariaLabel="Required skills"
              placeholder="Type a skill and press Enter" invalid={!!errors.skills} />
            {errors.skills && <span className="ui-field-error" role="alert">{errors.skills}</span>}
          </div>
          <Field label="Minimum experience (years)" error={errors.minExperience}>
            <Input type="number" inputMode="numeric" min={0} max={60} step={1} value={form.minExperience} onChange={(e) => set('minExperience', e.target.value)} aria-invalid={!!errors.minExperience} />
          </Field>
          <Field label="Description" className="sm:col-span-2" error={errors.description}>
            <Textarea rows={3} maxLength={20000} value={form.description} onChange={(e) => set('description', e.target.value)} placeholder="What the role involves and who suits it" />
          </Field>
        </FormSection>

        <FormSection icon={<MapPin size={18} />} title="Location and pay">
          <Field label="Country" error={errors.country}>
            <ComboInput value={form.country} onChange={(value) => set('country', value)} options={countries} ariaLabel="Country" placeholder="Start typing a country" maxLength={100}
              prefix={<Flag country={form.country} />} renderOption={(name) => <Flag country={name} />} normalize={properCountry} noMatchNote={() => 'Any country name can be typed.'} />
          </Field>
          <Field label="City" error={errors.city}><Input maxLength={100} value={form.city} onChange={(e) => set('city', e.target.value)} placeholder="Dubai" /></Field>
          <Field label="Salary (per month)" error={errors.salary}>
            <Input type="number" inputMode="decimal" min={0} max={9999999999.99} step={0.01} value={form.salary} onChange={(e) => set('salary', e.target.value)} placeholder="1800" aria-invalid={!!errors.salary} />
          </Field>
          <Field label="Currency" required error={errors.currency}>
            <Select value={form.currency} onChange={(e) => set('currency', e.target.value)} aria-invalid={!!errors.currency}>
              {!form.currency && <option value="">Choose a currency</option>}
              {currencyChoices(store.settings.currencies, order?.currency).map((currency) => <option key={currency.code} value={currency.code}>{currencyLabel(currency)}</option>)}
            </Select>
          </Field>
          <Field label="Contract period" error={errors.contractPeriod}><Input maxLength={60} value={form.contractPeriod} onChange={(e) => set('contractPeriod', e.target.value)} placeholder="2 years" /></Field>
          <Field label="Benefits (separate with commas)" className="sm:col-span-2" error={errors.benefits}>
            <Input value={form.benefits} onChange={(e) => set('benefits', e.target.value)} placeholder="Food, Accommodation" />
          </Field>
        </FormSection>

        <FormSection icon={<CalendarDays size={18} />} title="Interview">
          <Field label="Interview date" error={errors.interviewDate}><DatePicker value={form.interviewDate} onChange={(value) => set('interviewDate', value)} /></Field>
        </FormSection>
      </fieldset>
    </form>
  </Dialog>
}
