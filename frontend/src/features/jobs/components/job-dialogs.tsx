import { useState } from 'react'
import { api } from '@/shared/api/client'
import type { Designation, Order } from '@/shared/model/types'
import { useStore } from '@/shared/state/crm-store'
import { Button, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { DatePicker } from '@/shared/ui/date-picker'
import { Dialog } from '@/shared/ui/overlay'
import { refreshMasterData } from '@/features/master-data/components/record-audit'

export function DesignationDialog({ designation, onClose, onSaved }: { designation?: Designation; onClose: () => void; onSaved: (id: string) => void }) {
  const store = useStore()
  const [form, setForm] = useState({ title: designation?.title ?? '', category: designation?.category ?? '',
    description: designation?.description ?? '', skills: designation?.requiredSkills.join(', ') ?? '', minExperience: String(designation?.minExperience ?? 0) })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const set = (key: string, value: string) => setForm((previous) => ({ ...previous, [key]: value }))
  const close = () => { if (!saving) onClose() }
  const save = async () => {
    if (saving) return
    setSaving(true); setError('')
    try {
      const payload = { title: form.title, category: form.category || 'General', description: form.description,
        requiredSkills: form.skills.split(',').map((skill) => skill.trim()).filter(Boolean), minExperience: Number(form.minExperience) }
      const record = designation ? await api.put<Designation>('/designations/' + designation.id, payload) : await api.post<Designation>('/designations', payload)
      await refreshMasterData(store, designation ? 'Designation updated' : 'Designation created')
      if (store.isCurrentSession()) onSaved(record.id)
      onClose()
    } catch (err) { setError(err instanceof Error ? err.message : 'Designation could not be saved') }
    finally { setSaving(false) }
  }
  return <Dialog open onClose={close} title={designation ? 'Edit designation' : 'New designation'} description="Job titles, skills and experience help the team match candidates to orders."
    footer={<><Button variant="ghost" disabled={saving} onClick={close}>Cancel</Button><Button variant="primary" type="submit" form="designation-form" disabled={saving}>{saving ? 'Saving…' : 'Save designation'}</Button></>}>
    {error && <p role="alert" className="mb-4 rounded-control bg-stamp-50 p-3 text-sm text-stamp-700">{error}</p>}
    <form id="designation-form" onSubmit={(event) => { event.preventDefault(); void save() }}><fieldset disabled={saving} className="grid gap-4 sm:grid-cols-2">
      <Field label="Title" required className="sm:col-span-2"><Input required maxLength={160} value={form.title} onChange={(e) => set('title', e.target.value)} placeholder="Welder (6G)" /></Field>
      <Field label="Category"><Input maxLength={120} value={form.category} onChange={(e) => set('category', e.target.value)} /></Field>
      <Field label="Minimum experience (years)"><Input required type="number" min={0} max={60} step={1} value={form.minExperience} onChange={(e) => set('minExperience', e.target.value)} /></Field>
      <Field label="Description" className="sm:col-span-2"><Textarea maxLength={20000} value={form.description} onChange={(e) => set('description', e.target.value)} /></Field>
      <Field label="Required skills" hint="Separate with commas. Matching uses these saved skills." className="sm:col-span-2"><Input value={form.skills} onChange={(e) => set('skills', e.target.value)} placeholder="TIG, MIG, Blueprint reading" /></Field>
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
      await refreshMasterData(store, 'Designation deleted')
      onDeleted(); onClose()
    } catch (err) { setError(err instanceof Error ? err.message : 'Designation could not be deleted') }
    finally { setSaving(false) }
  }
  return <Dialog open onClose={() => { if (!saving) onClose() }} title="Delete designation?"
    description={designation.title} footer={<><Button disabled={saving} onClick={onClose}>Keep designation</Button><Button variant="danger" disabled={saving || linked > 0} onClick={() => void remove()}>{saving ? 'Deleting…' : 'Delete designation'}</Button></>}>
    <p className="text-sm text-ink-600">{linked ? 'This designation has linked orders and cannot be deleted. Edit its details to keep recruitment history intact.' : 'This removes the designation from the job catalog. Candidate skill and designation tags are preserved.'}</p>
    {error && <p role="alert" className="mt-4 text-sm text-stamp-700">{error}</p>}
  </Dialog>
}

export function OrderDialog({ designation, order, onClose }: { designation?: Designation; order?: Order; onClose: () => void }) {
  const store = useStore()
  const [form, setForm] = useState({ companyId: order?.companyId ?? '', designationId: order?.designationId ?? designation?.id ?? '',
    openings: String(order?.openings ?? 1), country: order?.country ?? '', city: order?.city ?? '',
    salary: order?.salary === undefined || order?.salary === null ? '' : String(order.salary), currency: order?.currency ?? 'AED',
    benefits: order?.benefits.join(', ') ?? '', contractPeriod: order?.contractPeriod ?? '',
    interviewDate: order?.interviewDate ?? '', deadline: order?.deadline ?? '', status: order?.status ?? 'open' })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const history = order ? store.applications.filter((application) => application.orderId === order.id) : []
  const deployed = history.filter((application) => application.status === 'deployed').length
  const active = history.filter((application) => application.status === 'active').length
  const set = (key: string, value: string) => setForm((previous) => ({ ...previous, [key]: value }))
  const close = () => { if (!saving) onClose() }
  const save = async () => {
    if (saving) return
    if (!form.companyId || !form.designationId) { setError('Choose a company and designation.'); return }
    if (form.interviewDate && form.deadline && form.interviewDate > form.deadline) { setError('The interview must be on or before the order deadline.'); return }
    setSaving(true); setError('')
    try {
      const payload = { ...form, openings: Number(form.openings), salary: form.salary === '' ? null : Number(form.salary),
        benefits: form.benefits.split(',').map((benefit) => benefit.trim()).filter(Boolean), interviewDate: form.interviewDate || null, deadline: form.deadline || null }
      if (order) await api.put('/orders/' + order.id, payload)
      else await api.post('/orders', payload)
      await refreshMasterData(store, order ? 'Order updated' : 'Order created')
      onClose()
    } catch (err) { setError(err instanceof Error ? err.message : 'Order could not be saved. Please try again.') }
    finally { setSaving(false) }
  }
  return <Dialog open onClose={close} width="lg" title={order ? 'Edit order · ' + order.ref : 'New recruitment order'}
    description={history.length ? 'Company and designation stay fixed once candidates are linked. Terms and availability can be updated.' : 'Set the employer, hiring requirements and employment terms.'}
    footer={<><Button variant="ghost" disabled={saving} onClick={close}>Cancel</Button><Button variant="primary" type="submit" form="order-form" disabled={saving}>{saving ? 'Saving…' : order ? 'Save order' : 'Create order'}</Button></>}>
    {error && <p role="alert" className="mb-4 rounded-control bg-stamp-50 p-3 text-sm text-stamp-700">{error}</p>}
    <form id="order-form" onSubmit={(event) => { event.preventDefault(); void save() }}><fieldset disabled={saving} className="grid gap-4 sm:grid-cols-2">
      <Field label="Company" required className="sm:col-span-2"><Select disabled={history.length > 0} value={form.companyId} onChange={(e) => set('companyId', e.target.value)}><option value="">Choose a company</option>{store.companies.filter((company) => company.id === form.companyId || (company.status === 'active' && store.client(company.clientId)?.status === 'active')).map((company) => <option key={company.id} value={company.id}>{company.name} · {store.client(company.clientId)?.name ?? 'Client'}</option>)}</Select></Field>
      <Field label="Designation" required className="sm:col-span-2"><Select disabled={history.length > 0} value={form.designationId} onChange={(e) => set('designationId', e.target.value)}><option value="">Choose a designation</option>{store.designations.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</Select></Field>
      <Field label="Openings" required hint={deployed ? deployed + ' candidates already deployed' : undefined}><Input required type="number" min={Math.max(1, deployed)} max={100000} step={1} value={form.openings} onChange={(e) => set('openings', e.target.value)} /></Field>
      <Field label="Status" hint="On hold pauses new matching. Closing requires no active applications."><Select value={form.status} onChange={(e) => set('status', e.target.value)}><option value="open">Open</option><option value="on_hold">On hold</option><option value="filled" disabled={deployed < Number(form.openings)}>Filled</option><option value="closed" disabled={active > 0}>Closed</option></Select></Field>
      <Field label="Work country"><Input maxLength={100} value={form.country} onChange={(e) => set('country', e.target.value)} /></Field>
      <Field label="Work city"><Input maxLength={100} value={form.city} onChange={(e) => set('city', e.target.value)} /></Field>
      <Field label="Salary"><Input type="number" min={0} max={9999999999.99} step={0.01} value={form.salary} onChange={(e) => set('salary', e.target.value)} /></Field>
      <Field label="Currency"><Input required pattern="[A-Z]{3}" maxLength={3} value={form.currency} onChange={(e) => set('currency', e.target.value.toUpperCase())} placeholder="AED" /></Field>
      <Field label="Contract period" className="sm:col-span-2"><Input maxLength={60} value={form.contractPeriod} onChange={(e) => set('contractPeriod', e.target.value)} placeholder="2 years" /></Field>
      <Field label="Interview date"><DatePicker value={form.interviewDate} onChange={(value) => set('interviewDate', value)} /></Field>
      <Field label="Order deadline"><DatePicker value={form.deadline} onChange={(value) => set('deadline', value)} /></Field>
      <Field label="Benefits" hint="Separate with commas" className="sm:col-span-2"><Input value={form.benefits} onChange={(e) => set('benefits', e.target.value)} placeholder="Accommodation, Transport, Medical" /></Field>
    </fieldset></form>
  </Dialog>
}
