import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { BriefcaseBusiness, Building2, Globe2, Plus, ShieldCheck, Trash2 } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import type { Company } from '@/shared/model/types'
import { DataTable, type Column } from '@/shared/ui/table'
import { EmptyState, PageHeader, Panel, PanelHeader, StatusPill } from '@/shared/ui/display'
import { RecordIdentity, SummaryCards } from '@/shared/ui/workspace'
import { Button, Field, Input, SearchInput, Select, Textarea } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { FileDrop, FileLink } from '@/shared/ui/file-drop'
import { api } from '@/shared/api/client'
import { DeleteRecordDialog } from '@/shared/components/delete-dialog'

/**
 * Companies are the employers orders are placed for. Each belongs to a client, so one
 * person can hold several businesses.
 */
export default function CompaniesPage() {
  const store = useStore()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [clientFilter, setClientFilter] = useState('all')
  const [adding, setAdding] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    return store.companies.filter((c) => {
      if (clientFilter !== 'all' && c.clientId !== clientFilter) return false
      if (!q) return true
      return (
        c.name.toLowerCase().includes(q) ||
        (c.industry ?? '').toLowerCase().includes(q) ||
        (c.city ?? '').toLowerCase().includes(q) ||
        (c.country ?? '').toLowerCase().includes(q) ||
        (store.client(c.clientId)?.name ?? '').toLowerCase().includes(q)
      )
    })
  }, [store.companies, store.clients, query, clientFilter])

  const columns: Column<Company>[] = [
    {
      key: 'name',
      header: 'Company',
      sortable: true,
      width: '22%',
      value: (c) => c.name,
      render: (c) => (
        <RecordIdentity name={c.name} detail={c.industry ?? 'Industry not recorded'} icon={Building2} />
      ),
    },
    {
      key: 'client',
      header: 'Client',
      sortable: true,
      filterable: true,
      className: 'whitespace-nowrap',
      value: (c) => store.client(c.clientId)?.name ?? '',
    },
    { key: 'city', header: 'City', sortable: true, filterable: true, value: (c) => c.city ?? '' },
    {
      key: 'country',
      header: 'Country',
      sortable: true,
      filterable: true,
      value: (c) => c.country ?? '',
    },
    {
      key: 'contact',
      header: 'Contact',
      className: 'whitespace-nowrap',
      value: (c) => c.contactPerson ?? '',
    },
    {
      key: 'phone',
      header: 'Phone',
      className: 'whitespace-nowrap',
      value: (c) => c.phone ?? '',
      render: (c) =>
        c.phone ? <span className="ident text-ink-600">{c.phone}</span> : <span className="text-ink-300">—</span>,
    },
    {
      key: 'orders',
      header: 'Open orders',
      align: 'right',
      sortable: true,
      value: (c) => store.orders.filter((o) => o.companyId === c.id && o.status === 'open').length,
      render: (c) => (
        <span className="tnum">
          {store.orders.filter((o) => o.companyId === c.id && o.status === 'open').length}
        </span>
      ),
    },
    {
      key: 'agreement',
      header: 'Agreement',
      className: 'whitespace-nowrap',
      value: (c) => (c.agreement ? 'Uploaded' : ''),
      render: (c) =>
        c.agreement ? (
          <FileLink url={c.agreement.url} title={c.agreement.name}>
            View agreement
          </FileLink>
        ) : (
          <span className="text-ink-300">—</span>
        ),
    },
    {
      key: 'status',
      header: 'Status',
      filterable: true,
      value: (c) => c.status,
      render: (c) => <StatusPill status={c.status} />,
    },
    ...(store.can('delete_records')
      ? [
          {
            key: 'delete',
            header: '',
            align: 'right' as const,
            render: (c: Company) => (
              <button
                onClick={(e) => {
                  // The row itself opens the client; this must not.
                  e.stopPropagation()
                  setDeletingId(c.id)
                }}
                aria-label={`Delete ${c.name}`}
                title="Delete company"
                className="rounded-control p-1.5 text-ink-400 hover:bg-stamp-50 hover:text-stamp-600"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            ),
          },
        ]
      : []),
  ]

  return (
    <>
      <PageHeader
        title="Companies"
        eyebrow="Employer network"
        description="A clear view of your employers, their contacts, and the opportunities they create."
        actions={
          <>
            <Button
              variant="primary"
              icon={<Plus className="h-4 w-4" />}
              onClick={() => setAdding(true)}
              disabled={store.clients.length === 0}
            >
              New company
            </Button>
          </>
        }
      />

      <SummaryCards
        items={[
          {
            label: 'Total companies',
            value: store.companies.length,
            detail: 'In your employer network',
            icon: Building2,
          },
          {
            label: 'Active employers',
            value: store.companies.filter((company) => company.status === 'active').length,
            detail: 'Open for collaboration',
            icon: ShieldCheck,
            tone: 'green',
          },
          {
            label: 'Open orders',
            value: store.orders.filter((order) => order.status === 'open').length,
            detail: 'Recruitment in progress',
            icon: BriefcaseBusiness,
            tone: 'violet',
          },
          {
            label: 'Countries',
            value: new Set(store.companies.map((company) => company.country?.trim()).filter(Boolean))
              .size,
            detail: 'Global hiring destinations',
            icon: Globe2,
            tone: 'amber',
          },
        ]}
      />

      <Panel flush>
        <PanelHeader title="Company directory" meta="The businesses behind your job orders" />
        <div className="workspace-list-toolbar">
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="Search companies, industries, or cities..."
            className="max-w-sm flex-1"
          />
          <Select value={clientFilter} onChange={(event) => setClientFilter(event.target.value)} className="w-48" aria-label="Filter by client">
            <option value="all">All clients</option>
            {store.clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
          </Select>
          <span className="ml-auto text-xs text-ink-400 tnum">{rows.length} companies</span>
        </div>
        <DataTable
          rows={rows}
          columns={columns}
          rowKey={(c) => c.id}
          onRowClick={(c) => navigate(`/clients/${c.clientId}`)}
          empty={
            <EmptyState
              title={store.clients.length === 0 ? 'Add a client first' : 'No companies yet'}
              description={
                store.clients.length === 0
                  ? 'A company belongs to a client, so the person has to exist before the business does.'
                  : 'Add the employer a job order will be placed for.'
              }
              action={
                store.clients.length === 0 ? (
                  <Button variant="primary" onClick={() => navigate('/clients')}>
                    Go to Clients
                  </Button>
                ) : (
                  <Button variant="primary" onClick={() => setAdding(true)}>
                    New company
                  </Button>
                )
              }
            />
          }
        />
      </Panel>

      <CompanyDialog open={adding} onClose={() => setAdding(false)} />
      <DeleteRecordDialog
        kind="company"
        id={deletingId ?? ''}
        open={!!deletingId}
        onClose={() => setDeletingId(null)}
      />
    </>
  )
}

function CompanyDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const store = useStore()
  const empty = {
    clientId: '',
    name: '',
    country: '',
    city: '',
    industry: '',
    contactPerson: '',
    contactEmail: '',
    phone: '',
    address: '',
    notes: '',
    status: 'active' as const,
  }
  const [form, setForm] = useState(empty)
  // The agreement is chosen before the company exists, so it waits for an id.
  const [agreement, setAgreement] = useState<File | null>(null)
  const [saving, setSaving] = useState(false)
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }))

  return (
    <Dialog
      open={open}
      onClose={onClose}
      width="lg"
      title="New company"
      description="The employer an order will be placed for."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!form.name.trim() || !form.clientId || saving}
            onClick={async () => {
              setSaving(true)
              try {
                const created = await store.addCompany(form)
                if (created && agreement) {
                  await api.upload('company', created.id, 'agreement', agreement)
                }
                setForm(empty)
                setAgreement(null)
                onClose()
              } finally {
                setSaving(false)
              }
            }}
          >
            {saving ? 'Saving…' : 'Save company'}
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Client" required hint="The person who owns this company" className="sm:col-span-2">
          <Select value={form.clientId} onChange={(e) => set('clientId', e.target.value)}>
            <option value="">Choose a client</option>
            {store.clients
              .filter((c) => c.status === 'active')
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                  {c.city ? ` — ${c.city}` : ''}
                </option>
              ))}
          </Select>
        </Field>
        <Field label="Company name" required className="sm:col-span-2">
          <Input value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="Al Habtoor Contracting" />
        </Field>
        <Field label="Industry">
          <Input value={form.industry} onChange={(e) => set('industry', e.target.value)} placeholder="Construction" />
        </Field>
        <Field label="Status">
          <Select value={form.status} onChange={(e) => set('status', e.target.value)}>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </Select>
        </Field>
        <Field label="City">
          <Input value={form.city} onChange={(e) => set('city', e.target.value)} />
        </Field>
        <Field label="Country">
          <Input value={form.country} onChange={(e) => set('country', e.target.value)} />
        </Field>
        <Field label="Contact person">
          <Input value={form.contactPerson} onChange={(e) => set('contactPerson', e.target.value)} />
        </Field>
        <Field label="Contact email">
          <Input type="email" value={form.contactEmail} onChange={(e) => set('contactEmail', e.target.value)} />
        </Field>
        <Field label="Phone / WhatsApp" className="sm:col-span-2">
          <Input ident value={form.phone} onChange={(e) => set('phone', e.target.value)} />
        </Field>
        <Field label="Address" className="sm:col-span-2">
          <Textarea rows={2} value={form.address} onChange={(e) => set('address', e.target.value)} />
        </Field>
        <div className="sm:col-span-2">
          <FileDrop
            label="Agreement"
            help="The signed agreement with this employer. Uploaded once the company is saved."
            onFile={(f) => setAgreement(f)}
          />
        </div>
        <Field label="Notes" className="sm:col-span-2">
          <Textarea
            rows={2}
            value={form.notes}
            onChange={(e) => set('notes', e.target.value)}
            placeholder="Interview preferences, payment terms, anything the team should know"
          />
        </Field>
      </div>
    </Dialog>
  )
}
