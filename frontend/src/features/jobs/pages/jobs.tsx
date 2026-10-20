import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { BriefcaseBusiness, ChevronRight, Globe2, Layers3, Plus, Users } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { dateLabel, money } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { DatePicker } from '@/shared/ui/date-picker'
import type { Designation, Order } from '@/shared/model/types'
import { Badge, EmptyState, PageHeader, Panel, PanelHeader, StatusPill } from '@/shared/ui/display'
import { Button, Field, Input, SearchInput, Select, Textarea } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { DataTable, type Column } from '@/shared/ui/table'
import { RecordIdentity, SummaryCards } from '@/shared/ui/workspace'

export default function JobsPage() {
  const store = useStore()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [activeId, setActiveId] = useState(store.designations[0]?.id ?? '')
  const [addingDesignation, setAddingDesignation] = useState(false)
  const [addingOrder, setAddingOrder] = useState(false)

  const designations = useMemo(() => {
    const q = query.trim().toLowerCase()
    return store.designations.filter(
      (d) => !q || d.title.toLowerCase().includes(q) || d.category.toLowerCase().includes(q),
    )
  }, [store.designations, query])

  const active = store.designations.find((d) => d.id === activeId)
  const orders = store.orders.filter((o) => o.designationId === activeId)

  const orderColumns: Column<Order>[] = [
    {
      key: 'client',
      header: 'Client',
      sortable: true,
      width: '24%',
      value: (o) => store.company(o.companyId)?.name ?? '',
      render: (o) => (
        <RecordIdentity name={store.company(o.companyId)?.name ?? 'Unknown company'} detail={o.ref} icon={BriefcaseBusiness} />
      ),
    },
    {
      key: 'location',
      header: 'Location',
      sortable: true,
      filterable: true,
      className: 'whitespace-nowrap',
      value: (o) => `${o.city}, ${o.country}`,
    },
    {
      key: 'salary',
      header: 'Salary',
      sortable: true,
      align: 'right',
      className: 'whitespace-nowrap',
      value: (o) => o.salary,
      render: (o) => <span className="tnum">{money(o.salary, o.currency)}</span>,
    },
    {
      key: 'contract',
      header: 'Contract',
      filterable: true,
      className: 'whitespace-nowrap',
      value: (o) => o.contractPeriod,
    },
    {
      key: 'openings',
      header: 'Openings',
      align: 'right',
      sortable: true,
      value: (o) => o.openings,
      render: (o) => <span className="tnum">{o.openings}</span>,
    },
    {
      key: 'matched',
      header: 'In process',
      align: 'right',
      sortable: true,
      value: (o) => store.applications.filter((a) => a.orderId === o.id && a.status === 'active').length,
      render: (o) => (
        <span className="tnum">
          {store.applications.filter((a) => a.orderId === o.id && a.status === 'active').length}
        </span>
      ),
    },
    {
      key: 'deployed',
      header: 'Deployed',
      align: 'right',
      sortable: true,
      value: (o) => store.applications.filter((a) => a.orderId === o.id && a.status === 'deployed').length,
      render: (o) => {
        const n = store.applications.filter((a) => a.orderId === o.id && a.status === 'deployed').length
        return (
          <span className={cn('tnum', n > 0 ? 'font-medium text-ink' : 'text-ink-400')}>
            {n}/{o.openings}
          </span>
        )
      },
    },
    {
      key: 'deadline',
      header: 'Closes',
      sortable: true,
      className: 'whitespace-nowrap',
      value: (o) => o.deadline,
      render: (o) => <span className="text-ink-500">{dateLabel(o.deadline)}</span>,
    },
    {
      key: 'status',
      header: 'Status',
      filterable: true,
      value: (o) => o.status,
      render: (o) => <StatusPill status={o.status} />,
    },
  ]

  return (
    <>
      <PageHeader
        title="Jobs / Positions"
        eyebrow="Hiring opportunities"
        description="Connect the right people to the right opportunity. Manage designations and client orders."
        actions={
          <>
            <Button variant="secondary" icon={<Plus className="h-4 w-4" />} onClick={() => setAddingDesignation(true)}>
              New designation
            </Button>
            <Button
              variant="primary"
              icon={<Plus className="h-4 w-4" />}
              disabled={!active}
              onClick={() => setAddingOrder(true)}
            >
              New order
            </Button>
          </>
        }
      />

      <SummaryCards
        items={[
          {
            label: 'Designations',
            value: store.designations.length,
            detail: 'Specialisms you recruit for',
            icon: Layers3,
          },
          {
            label: 'Open orders',
            value: store.orders.filter((order) => order.status === 'open').length,
            detail: 'Active hiring requirements',
            icon: BriefcaseBusiness,
            tone: 'violet',
          },
          {
            label: 'Requested openings',
            value: store.orders
              .filter((order) => order.status === 'open')
              .reduce((total, order) => total + order.openings, 0),
            detail: 'Headcount across open orders',
            icon: Users,
            tone: 'green',
          },
          {
            label: 'Destinations',
            value: new Set(store.orders.map((order) => order.country).filter(Boolean)).size,
            detail: 'Countries you are hiring for',
            icon: Globe2,
            tone: 'amber',
          },
        ]}
      />

      <div className="workspace-split jobs-layout">
        <Panel flush className="designation-panel h-fit min-w-0">
          <PanelHeader title="Designations" meta={`${store.designations.length} job categories`} />
          <div className="border-b border-rule-soft p-3">
            <SearchInput value={query} onChange={setQuery} placeholder="Find a designation" />
          </div>
          <ul className="scrollbar-thin max-h-[32rem] overflow-y-auto">
            {designations.map((d) => {
              const count = store.orders.filter((o) => o.designationId === d.id && o.status === 'open').length
              return (
                <li key={d.id}>
                  <button
                    onClick={() => setActiveId(d.id)}
                    aria-pressed={d.id === activeId}
                    className={cn(
                      'designation-option',
                      'flex w-full items-center gap-2 border-b border-rule-soft px-3 py-2.5 text-left transition-colors last:border-0',
                      d.id === activeId ? 'bg-navy-50' : 'hover:bg-field/70',
                    )}
                  >
                    <span className="designation-icon"><BriefcaseBusiness size={17} strokeWidth={1.6} /></span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-ink">{d.title}</span>
                      <span className="block truncate text-micro text-ink-400">{d.category}</span>
                    </span>
                    {count > 0 && (
                      <span className="shrink-0 rounded-[3px] bg-field px-1 py-px text-micro text-ink-500 tnum">
                        {count}
                      </span>
                    )}
                    <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-300" />
                  </button>
                </li>
              )
            })}
            {designations.length === 0 && (
              <EmptyState title="No designations match" description="Try another search." />
            )}
          </ul>
        </Panel>

        <div className="min-w-0 space-y-4">
          {active ? (
            <>
              <Panel className="designation-detail">
                <span className="workspace-eyebrow">Selected designation</span>
                <h2 className="text-h2 font-semibold text-ink">{active.title}</h2>
                <p className="mt-1 max-w-[70ch] text-sm text-ink-500">{active.description}</p>
                <div className="mt-3 flex flex-wrap items-center gap-1.5">
                  <Badge>{active.category}</Badge>
                  <Badge>Minimum {active.minExperience} years</Badge>
                  {active.requiredSkills.map((s) => (
                    <Badge key={s} tone="navy">
                      {s}
                    </Badge>
                  ))}
                </div>
              </Panel>

              <Panel flush>
                <PanelHeader
                  title="Orders"
                  meta={`${orders.length} under this designation`}
                  action={
                    <Button size="sm" variant="secondary" icon={<Plus className="h-3.5 w-3.5" />} onClick={() => setAddingOrder(true)}>
                      New order
                    </Button>
                  }
                />
                {orders.length === 0 ? (
                  <EmptyState
                    title="No orders for this designation yet"
                    description="An order is one client's opening — openings, salary, country and a deadline."
                    action={<Button variant="primary" onClick={() => setAddingOrder(true)}>Create the first order</Button>}
                  />
                ) : (
                  <DataTable
                    rows={orders}
                    columns={orderColumns}
                    rowKey={(o) => o.id}
                    onRowClick={(o) => navigate(`/orders/${o.id}`)}
                    pageSize={10}
                  />
                )}
              </Panel>
            </>
          ) : (
            <Panel>
              <EmptyState title="Pick a designation" description="Choose one on the left to see its orders." />
            </Panel>
          )}
        </div>
      </div>

      <DesignationDialog open={addingDesignation} onClose={() => setAddingDesignation(false)} />
      {active && (
        <OrderDialog open={addingOrder} onClose={() => setAddingOrder(false)} designation={active} />
      )}
    </>
  )
}

function DesignationDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { addDesignation } = useStore()
  const [form, setForm] = useState({ title: '', category: '', description: '', skills: '', minExperience: '2' })
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }))

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="New designation"
      description="The job title candidates and orders are matched on."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!form.title.trim()}
            onClick={() => {
              void addDesignation({
                title: form.title.trim(),
                category: form.category.trim() || 'General',
                description: form.description.trim(),
                requiredSkills: form.skills.split(',').map((s) => s.trim()).filter(Boolean),
                minExperience: Number(form.minExperience) || 0,
              })
              setForm({ title: '', category: '', description: '', skills: '', minExperience: '2' })
              onClose()
            }}
          >
            Save designation
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Title" required className="sm:col-span-2">
          <Input value={form.title} onChange={(e) => set('title', e.target.value)} placeholder="Welder (6G)" />
        </Field>
        <Field label="Category">
          <Input value={form.category} onChange={(e) => set('category', e.target.value)} placeholder="Fabrication" />
        </Field>
        <Field label="Minimum experience (years)">
          <Input type="number" value={form.minExperience} onChange={(e) => set('minExperience', e.target.value)} />
        </Field>
        <Field label="Description" className="sm:col-span-2">
          <Textarea value={form.description} onChange={(e) => set('description', e.target.value)} />
        </Field>
        <Field label="Required skills" hint="Separate with commas" className="sm:col-span-2">
          <Input value={form.skills} onChange={(e) => set('skills', e.target.value)} placeholder="TIG, MIG, Blueprint reading" />
        </Field>
      </div>
    </Dialog>
  )
}

function OrderDialog({
  open,
  onClose,
  designation,
}: {
  open: boolean
  onClose: () => void
  designation: Designation
}) {
  const store = useStore()
  const [form, setForm] = useState({
    companyId: '',
    openings: '5',
    country: '',
    city: '',
    salary: '',
    currency: 'AED',
    benefits: '',
    contractPeriod: '2 years',
    interviewDate: '',
    deadline: '',
  })
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }))

  return (
    <Dialog
      open={open}
      onClose={onClose}
      width="lg"
      title={`New order — ${designation.title}`}
      description="One client's opening under this designation."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!form.companyId || !form.deadline}
            onClick={() => {
              void store.addOrder({
                companyId: form.companyId,
                designationId: designation.id,
                openings: Number(form.openings) || 1,
                country: form.country,
                city: form.city,
                salary: Number(form.salary) || 0,
                currency: form.currency,
                benefits: form.benefits.split(',').map((b) => b.trim()).filter(Boolean),
                contractPeriod: form.contractPeriod,
                interviewDate: form.interviewDate || undefined,
                deadline: form.deadline,
              })
              onClose()
            }}
          >
            Create order
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Company" required hint="The employer this order is for" className="sm:col-span-2">
          <Select value={form.companyId} onChange={(e) => set('companyId', e.target.value)}>
            <option value="">Choose a company</option>
            {store.companies
              .filter((c) => c.status === 'active')
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} — {store.client(c.clientId)?.name ?? 'no client'}
                </option>
              ))}
          </Select>
        </Field>
        <Field label="Number of openings">
          <Input type="number" value={form.openings} onChange={(e) => set('openings', e.target.value)} />
        </Field>
        <Field label="Contract period">
          <Input value={form.contractPeriod} onChange={(e) => set('contractPeriod', e.target.value)} />
        </Field>
        <Field label="Work country">
          <Input value={form.country} onChange={(e) => set('country', e.target.value)} placeholder="United Arab Emirates" />
        </Field>
        <Field label="Work city">
          <Input value={form.city} onChange={(e) => set('city', e.target.value)} placeholder="Dubai" />
        </Field>
        <Field label="Salary">
          <Input type="number" value={form.salary} onChange={(e) => set('salary', e.target.value)} />
        </Field>
        <Field label="Currency">
          <Select value={form.currency} onChange={(e) => set('currency', e.target.value)}>
            {['AED', 'SAR', 'QAR', 'KWD', 'OMR', 'BHD'].map((c) => (
              <option key={c}>{c}</option>
            ))}
          </Select>
        </Field>
        <Field label="Interview date">
          <DatePicker value={form.interviewDate} onChange={(value) => set('interviewDate', value)} />
        </Field>
        <Field label="Order deadline" required>
          <DatePicker required value={form.deadline} onChange={(value) => set('deadline', value)} />
        </Field>
        <Field label="Benefits" hint="Separate with commas" className="sm:col-span-2">
          <Input value={form.benefits} onChange={(e) => set('benefits', e.target.value)} placeholder="Accommodation, Transport, Medical" />
        </Field>
      </div>
    </Dialog>
  )
}
