import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Building2, Globe2, Plus, UserCheck, Users } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import type { Client } from '@/shared/model/types'
import { DataTable, type Column } from '@/shared/ui/table'
import { EmptyState, PageHeader, Panel, PanelHeader, StatusPill } from '@/shared/ui/display'
import { RecordIdentity, SummaryCards } from '@/shared/ui/workspace'
import { Button, SearchInput } from '@/shared/ui/primitives'
import { ClientDialog } from '@/features/clients/components/client-dialog'

/**
 * Clients are people. The businesses they own are companies, and an order is placed
 * for a company — so one client can appear behind several employers.
 */
export default function ClientsPage() {
  const store = useStore()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [adding, setAdding] = useState(false)

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    return store.clients.filter(
      (c) =>
        !q ||
        c.name.toLowerCase().includes(q) ||
        (c.email ?? '').toLowerCase().includes(q) ||
        (c.phone ?? '').includes(q) ||
        (c.city ?? '').toLowerCase().includes(q),
    )
  }, [store.clients, query])

  const columns: Column<Client>[] = [
    {
      key: 'name',
      header: 'Client',
      sortable: true,
      width: '24%',
      value: (c) => c.name,
      render: (c) => (
        <RecordIdentity name={c.name} detail={c.email ?? 'No email on file'} />
      ),
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
      key: 'whatsapp',
      header: 'WhatsApp',
      className: 'whitespace-nowrap',
      value: (c) => c.whatsapp ?? '',
      render: (c) =>
        c.whatsapp ? (
          <span className="ident text-ink-600">{c.whatsapp}</span>
        ) : (
          <span className="text-ink-300">—</span>
        ),
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
      key: 'companies',
      header: 'Companies',
      align: 'right',
      sortable: true,
      value: (c) => store.companies.filter((co) => co.clientId === c.id).length,
      render: (c) => (
        <span className="tnum">{store.companies.filter((co) => co.clientId === c.id).length}</span>
      ),
    },
    {
      key: 'orders',
      header: 'Open orders',
      align: 'right',
      sortable: true,
      value: (c) => {
        const ids = store.companies.filter((co) => co.clientId === c.id).map((co) => co.id)
        return store.orders.filter((o) => ids.includes(o.companyId) && o.status === 'open').length
      },
      render: (c) => {
        const ids = store.companies.filter((co) => co.clientId === c.id).map((co) => co.id)
        return (
          <span className="tnum">
            {store.orders.filter((o) => ids.includes(o.companyId) && o.status === 'open').length}
          </span>
        )
      },
    },
    {
      key: 'status',
      header: 'Status',
      filterable: true,
      value: (c) => c.status,
      render: (c) => <StatusPill status={c.status} />,
    },
  ]

  return (
    <>
      <PageHeader
        title="Clients"
        eyebrow="Client relationships"
        description="Build strong relationships. Keep every client, contact, and company connected."
        actions={
          <>
            <Button variant="secondary" onClick={() => navigate('/companies')}>
              Companies
            </Button>
            <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setAdding(true)}>
              New client
            </Button>
          </>
        }
      />

      <SummaryCards
        items={[
          {
            label: 'Total clients',
            value: store.clients.length,
            detail: 'Across your client network',
            icon: Users,
          },
          {
            label: 'Active clients',
            value: store.clients.filter((client) => client.status === 'active').length,
            detail: 'Ready for new opportunities',
            icon: UserCheck,
            tone: 'green',
          },
          {
            label: 'Linked companies',
            value: store.companies.length,
            detail: 'Employers in your network',
            icon: Building2,
            tone: 'violet',
          },
          {
            label: 'Countries',
            value: new Set(store.clients.map((client) => client.country?.trim()).filter(Boolean)).size,
            detail: 'Your international reach',
            icon: Globe2,
            tone: 'amber',
          },
        ]}
      />

      <Panel flush>
        <PanelHeader title="Client directory" meta="Your contacts, all in one place" />
        <div className="workspace-list-toolbar">
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="Search clients by name, email, or city..."
            className="max-w-sm flex-1"
          />
          <span className="ml-auto text-xs text-ink-400 tnum">{rows.length} clients</span>
        </div>
        <DataTable
          rows={rows}
          columns={columns}
          rowKey={(c) => c.id}
          onRowClick={(c) => navigate(`/clients/${c.id}`)}
          empty={
            <EmptyState
              title="No clients yet"
              description="Add the person first, then the companies they own."
              action={<Button variant="primary" onClick={() => setAdding(true)}>New client</Button>}
            />
          }
        />
      </Panel>

      {adding && <ClientDialog onClose={() => setAdding(false)} />}
    </>
  )
}
