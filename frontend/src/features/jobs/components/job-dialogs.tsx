import { useMemo, useState, type ReactNode } from 'react'
import { BriefcaseBusiness, CalendarDays, Gift, MapPin, Users } from 'lucide-react'
import { api } from '@/shared/api/client'
import type { Designation, Order } from '@/shared/model/types'
import { useStore } from '@/shared/state/crm-store'
import { frequentValues } from '@/shared/lib/suggestions'
import { currencyChoices, currencyLabel } from '@/shared/lib/agency'
import { Badge } from '@/shared/ui/display'
import { Button, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { DatePicker } from '@/shared/ui/date-picker'
import { Dialog } from '@/shared/ui/overlay'
import { refreshMasterData } from '@/features/master-data/components/record-audit'
import { DialogMark } from '@/features/master-data/components/dialog-mark'
import '@/styles/jobs.css'

function FormSection({ icon, title, hint, children }: { icon: ReactNode; title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="form-section">
      <header>
        <span aria-hidden="true">{icon}</span>
        <div><h3>{title}</h3>{hint && <p>{hint}</p>}</div>
      </header>
      <div className="grid gap-4 sm:grid-cols-2">{children}</div>
    </section>
  )
}

export function DesignationDialog({ designation, onClose, onSaved }: { designation?: Designation; onClose: () => void; onSaved: (id: string) => void }) {
  const store = useStore()
  const [form, setForm] = useState({ title: designation?.title ?? '', category: designation?.category ?? '',
    description: designation?.description ?? '', skills: designation?.requiredSkills.join(', ') ?? '', minExperience: String(designation?.minExperience ?? 0) })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const categories = useMemo(() => frequentValues(store.designations.map((item) => item.category)), [store.designations])
  const set = (key: string, value: string) => setForm((previous) => ({ ...previous, [key]: value }))
  const close = () => { if (!saving) onClose() }
  const skillList = form.skills.split(',').map((skill) => skill.trim()).filter(Boolean)
  const save = async () => {
    if (saving) return
    setSaving(true); setError('')
    try {
      const payload = { title: form.title, category: form.category.trim() || null, description: form.description,
        requiredSkills: skillList, minExperience: Number(form.minExperience) }
      const record = designation ? await api.put<Designation>('/designations/' + designation.id, payload) : await api.post<Designation>('/designations', payload)
      await refreshMasterData(store, 'Job title saved')
      if (store.isCurrentSession()) onSaved(record.id)
      onClose()
    } catch (err) { setError(err instanceof Error ? err.message : 'We could not save this job title. Please try again.') }
    finally { setSaving(false) }
  }
  return <Dialog open onClose={close} width="lg" title={designation ? 'Edit job title' : 'New job title'} description="Orders from clients are created inside a job title. Its skills and experience are used to match candidates."
    footer={<><Button variant="ghost" disabled={saving} onClick={close}>Cancel</Button><Button variant="primary" type="submit" form="designation-form" disabled={saving}>{saving ? 'Saving…' : 'Save job title'}</Button></>}>
    <DialogMark />
    {error && <p role="alert" className="mb-4 rounded-control bg-stamp-50 p-3 text-sm text-stamp-700">{error}</p>}
    <form id="designation-form" className="form-stack jv-form" onSubmit={(event) => { event.preventDefault(); void save() }}><fieldset disabled={saving} className="form-stack">
      <FormSection icon={<BriefcaseBusiness size={18} />} title="Basic details">
        <Field label="Job title" required className="sm:col-span-2"><Input required maxLength={160} value={form.title} onChange={(e) => set('title', e.target.value)} placeholder="Welder (6G)" /></Field>
        <Field label="Category"><Input maxLength={120} list="designation-categories" value={form.category} onChange={(e) => set('category', e.target.value)} placeholder="Skilled trades" /><datalist id="designation-categories">{categories.map((name) => <option key={name} value={name} />)}</datalist></Field>
        <Field label="Minimum experience (years)"><Input required type="number" min={0} max={60} step={1} value={form.minExperience} onChange={(e) => set('minExperience', e.target.value)} /></Field>
        <Field label="Description" className="sm:col-span-2"><Textarea maxLength={20000} value={form.description} onChange={(e) => set('description', e.target.value)} placeholder="What the role involves and who suits it" /></Field>
      </FormSection>
      <FormSection icon={<Users size={18} />} title="Required skills" hint="We compare these with the skills saved on each candidate">
        <Field label="Skills" hint="Separate with commas." className="sm:col-span-2"><Input value={form.skills} onChange={(e) => set('skills', e.target.value)} placeholder="TIG, MIG, Blueprint reading" /></Field>
        {skillList.length > 0 && <div className="sm:col-span-2 flex flex-wrap gap-1.5">{skillList.map((skill) => <Badge key={skill}>{skill}</Badge>)}</div>}
      </FormSection>
    </fieldset></form>
  </Dialog>
}

export function RemoveDesignationDialog({ designation, onClose, onDeleted }: { designation: Designation; onClose: () => void; onDeleted: () => void }) {
  const store = useStore()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const linked = store.orders.filter((order) => order.designationId === designation.id).length
  const remove = async () => {
    setSaving(true); setError('')
    try {
      await api.del('/designations/' + designation.id)
      await refreshMasterData(store, 'Job title deleted')
      onDeleted(); onClose()
    } catch (err) { setError(err instanceof Error ? err.message : 'We could not delete this job title. Please try again.') }
    finally { setSaving(false) }
  }
  return <Dialog open onClose={() => { if (!saving) onClose() }} title="Delete this job title?"
    description={designation.title} footer={<><Button disabled={saving} onClick={onClose}>Keep job title</Button><Button variant="danger" disabled={saving || linked > 0} onClick={() => void remove()}>{saving ? 'Deleting…' : 'Delete job title'}</Button></>}>
    <DialogMark />
    <p className="text-sm text-ink-600">{linked ? `This job title has ${linked} ${linked === 1 ? 'order' : 'orders'}, so it cannot be deleted. Edit it instead to keep your records.` : 'This removes the job title from your list. Skills and job titles already saved on candidates stay as they are.'}</p>
    {error && <p role="alert" className="mt-4 text-sm text-stamp-700">{error}</p>}
  </Dialog>
}

/** Removing a candidate from an order closes their application there; the candidate is not deleted. */
export function UnmapCandidateDialog({ name, saving, error, onClose, onConfirm }: { name: string; saving: boolean; error: string; onClose: () => void; onConfirm: () => void }) {
  return <Dialog open onClose={() => { if (!saving) onClose() }} title="Remove this candidate from the order?"
    description="This closes their application on this order and cancels its open tasks. The candidate goes back to the Data Bank and can be matched to another order."
    footer={<><Button variant="ghost" disabled={saving} onClick={onClose}>Keep on order</Button><Button variant="danger" disabled={saving} onClick={onConfirm}>{saving ? 'Removing…' : 'Remove from order'}</Button></>}>
    <DialogMark />
    <p className="text-sm text-ink-600">Stages {name} has already done stay on record. If you match them again later, they start again at Stage 1.</p>
    {error && <p role="alert" className="mt-3 text-sm text-stamp-700">{error}</p>}
  </Dialog>
}

/**
 * Orders are raised inside a designation: when one is passed the role is fixed and shown as
 * context, and only the client and the terms are chosen. Opening it without one (from a
 * client's page) still lets the role be picked.
 */
export function OrderDialog({ designation, order, companyId, onClose, onSaved }: { designation?: Designation; order?: Order; companyId?: string; onClose: () => void; onSaved?: (id: string) => void }) {
  const store = useStore()
  const [form, setForm] = useState({ companyId: order?.companyId ?? companyId ?? '', designationId: order?.designationId ?? designation?.id ?? '',
    openings: String(order?.openings ?? 1), country: order?.country ?? '', city: order?.city ?? '',
    salary: order?.salary === undefined || order?.salary === null ? '' : String(order.salary), currency: order?.currency ?? store.settings.defaultOrderCurrency,
    benefits: order?.benefits.join(', ') ?? '', contractPeriod: order?.contractPeriod ?? '',
    interviewDate: order?.interviewDate ?? '', deadline: order?.deadline ?? '', status: order?.status ?? 'open' })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const history = order ? store.applications.filter((application) => application.orderId === order.id) : []
  const deployed = history.filter((application) => application.status === 'deployed').length
  const active = history.filter((application) => application.status === 'active').length
  const role = store.designation(form.designationId)
  const roleLocked = history.length > 0 || (!!designation && !order)
  const clientLocked = history.length > 0 || (!!companyId && !order)
  const set = (key: string, value: string) => setForm((previous) => ({ ...previous, [key]: value }))
  const close = () => { if (!saving) onClose() }
  const benefitSuggestions = useMemo(() => frequentValues(store.orders.flatMap((item) => item.benefits), 12), [store.orders])
  const benefitList = form.benefits.split(',').map((benefit) => benefit.trim()).filter(Boolean)
  const toggleBenefit = (name: string) => {
    const has = benefitList.some((benefit) => benefit.toLowerCase() === name.toLowerCase())
    set('benefits', (has ? benefitList.filter((benefit) => benefit.toLowerCase() !== name.toLowerCase()) : [...benefitList, name]).join(', '))
  }
  const save = async () => {
    if (saving) return
    if (!form.companyId || !form.designationId) { setError('Choose a company and a job title.'); return }
    if (form.interviewDate && form.deadline && form.interviewDate > form.deadline) { setError('The interview date must be on or before the order deadline.'); return }
    setSaving(true); setError('')
    try {
      const payload = { ...form, openings: Number(form.openings), salary: form.salary === '' ? null : Number(form.salary),
        benefits: benefitList, interviewDate: form.interviewDate || null, deadline: form.deadline || null }
      const record = order ? await api.put<Order>('/orders/' + order.id, payload) : await api.post<Order>('/orders', payload)
      await refreshMasterData(store, 'Order saved')
      if (store.isCurrentSession()) onSaved?.(record.id)
      onClose()
    } catch (err) { setError(err instanceof Error ? err.message : 'We could not save the order. Please try again.') }
    finally { setSaving(false) }
  }
  return <Dialog open onClose={close} width="lg" title={order ? 'Edit order · ' + order.ref : role && roleLocked ? `New order · ${role.title}` : 'New order'}
    description={history.length ? 'The company and job title cannot change once candidates are matched. You can still change the terms.' : 'One client\'s opening for a job title.'}
    footer={<><Button variant="ghost" disabled={saving} onClick={close}>Cancel</Button><Button variant="primary" type="submit" form="order-form" disabled={saving}>{saving ? 'Saving…' : order ? 'Save order' : 'Create order'}</Button></>}>
    <DialogMark />
    {error && <p role="alert" className="mb-4 rounded-control bg-stamp-50 p-3 text-sm text-stamp-700">{error}</p>}
    <form id="order-form" className="form-stack jv-form" onSubmit={(event) => { event.preventDefault(); void save() }}><fieldset disabled={saving} className="form-stack">
      <FormSection icon={<BriefcaseBusiness size={18} />} title="Job and company" hint="Which job, and which company is hiring">
        {roleLocked && role
          ? <div className="form-context sm:col-span-2"><div><p className="form-context-label">Job title</p><p className="form-context-title">{role.title}<span>{role.category ? role.category + ' · ' : ''}{role.minExperience > 0 ? `${role.minExperience}+ years experience` : 'Any experience'}</span></p></div>{role.requiredSkills.length > 0 && <div className="form-context-skills">{role.requiredSkills.map((skill) => <Badge key={skill}>{skill}</Badge>)}</div>}</div>
          : <Field label="Job title" required className="sm:col-span-2"><Select value={form.designationId} onChange={(e) => set('designationId', e.target.value)}><option value="">Choose a job title</option>{store.designations.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</Select></Field>}
        <Field label="Company" required className="sm:col-span-2" hint={history.length ? 'Cannot change because candidates are already matched.' : undefined}><Select disabled={clientLocked} value={form.companyId} onChange={(e) => set('companyId', e.target.value)}><option value="">Choose a company</option>{store.companies.filter((company) => company.id === form.companyId || (company.status === 'active' && store.client(company.clientId)?.status === 'active')).map((company) => <option key={company.id} value={company.id}>{company.name} · {store.client(company.clientId)?.name ?? 'Contact'}</option>)}</Select></Field>
      </FormSection>
      <FormSection icon={<Users size={18} />} title="Openings and status">
        <Field label="Number of openings" required hint={deployed ? deployed + ' already deployed' : undefined}><Input required type="number" min={Math.max(1, deployed)} max={100000} step={1} value={form.openings} onChange={(e) => set('openings', e.target.value)} /></Field>
        <Field label="Status" hint="On hold stops new matching. You can only close an order when no candidate is still active on it."><Select value={form.status} onChange={(e) => set('status', e.target.value)}><option value="open">Open</option><option value="on_hold">On hold</option><option value="filled" disabled={deployed < Number(form.openings)}>Filled</option><option value="closed" disabled={active > 0}>Closed</option></Select></Field>
      </FormSection>
      <FormSection icon={<MapPin size={18} />} title="Work location and pay">
        <Field label="Work country"><Input maxLength={100} value={form.country} onChange={(e) => set('country', e.target.value)} placeholder="United Arab Emirates" /></Field>
        <Field label="Work city"><Input maxLength={100} value={form.city} onChange={(e) => set('city', e.target.value)} placeholder="Dubai" /></Field>
        <Field label="Salary (per month)"><Input type="number" min={0} max={9999999999.99} step={0.01} value={form.salary} onChange={(e) => set('salary', e.target.value)} placeholder="1800" /></Field>
        <Field label="Currency" required hint="Currencies are managed under User Management, Agency settings."><Select required value={form.currency} onChange={(e) => set('currency', e.target.value)}>{!form.currency && <option value="">Choose a currency</option>}{currencyChoices(store.settings.currencies, order?.currency).map((currency) => <option key={currency.code} value={currency.code}>{currencyLabel(currency)}</option>)}</Select></Field>
      </FormSection>
      <FormSection icon={<CalendarDays size={18} />} title="Contract and dates">
        <Field label="Contract period" className="sm:col-span-2"><Input maxLength={60} value={form.contractPeriod} onChange={(e) => set('contractPeriod', e.target.value)} placeholder="2 years" /></Field>
        <Field label="Interview date"><DatePicker value={form.interviewDate} onChange={(value) => set('interviewDate', value)} /></Field>
        <Field label="Order deadline"><DatePicker value={form.deadline} onChange={(value) => set('deadline', value)} /></Field>
      </FormSection>
      <FormSection icon={<Gift size={18} />} title="Benefits" hint="Tap a benefit used on other orders to add it, or type your own separated by commas">
        {benefitSuggestions.length > 0 && <div className="sm:col-span-2 form-chips">{benefitSuggestions.map((name) => { const on = benefitList.some((benefit) => benefit.toLowerCase() === name.toLowerCase()); return <button type="button" key={name} aria-pressed={on} className={on ? 'is-on' : undefined} onClick={() => toggleBenefit(name)}>{name}</button> })}</div>}
        <Field label="Benefits (separate with commas)" className="sm:col-span-2"><Input value={form.benefits} onChange={(e) => set('benefits', e.target.value)} placeholder="Separate with commas" /></Field>
      </FormSection>
    </fieldset></form>
  </Dialog>
}
