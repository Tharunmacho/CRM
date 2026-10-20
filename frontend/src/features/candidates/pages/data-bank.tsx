import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CircleCheck, Download, FileSearch, UserCog, UserPlus, Users, UserSearch } from 'lucide-react'
import { dateLabel } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { stageByNo } from '@/shared/workflow/stages'
import type { Candidate } from '@/shared/model/types'
import { DataTable, type Column } from '@/shared/ui/table'
import { Badge, EmptyState, PageHeader, Panel, PanelHeader, StatusPill } from '@/shared/ui/display'
import { RecordIdentity, SummaryCards } from '@/shared/ui/workspace'
import { Button, SearchInput, Select } from '@/shared/ui/primitives'
import { Dialog, Tabs } from '@/shared/ui/overlay'

const SOURCE_LABEL: Record<string, string> = {
  email: 'Email',
  whatsapp: 'WhatsApp',
  walk_in: 'Walk-in',
  referral: 'Referral',
  other: 'Other',
}

export default function DataBankPage() {
  const store = useStore()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [pool, setPool] = useState<'all' | 'not_interested' | 'unmapped'>('all')
  const [selected, setSelected] = useState<string[]>([])
  const [reassigning, setReassigning] = useState(false)
  const [reassignTo, setReassignTo] = useState('')

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    return store.candidates.filter((c) => {
      if (pool === 'not_interested' && c.status !== 'not_interested') return false
      if (pool === 'unmapped' && store.activeApplicationFor(c.id)) return false
      if (!q) return true
      // Full-text search covers the parsed resume, not just the indexed columns.
      return (
        c.name.toLowerCase().includes(q) ||
        c.phone.includes(q) ||
        (c.passportNo ?? '').toLowerCase().includes(q) ||
        (c.email ?? '').toLowerCase().includes(q) ||
        c.designations.join(' ').toLowerCase().includes(q) ||
        c.skills.join(' ').toLowerCase().includes(q) ||
        c.resumeText.toLowerCase().includes(q)
      )
    })
  }, [store.candidates, store.applications, query, pool])

  const columns: Column<Candidate>[] = [
    {
      key: 'name',
      header: 'Candidate',
      sortable: true,
      value: (c) => c.name,
      width: '20%',
      render: (c) => (
        <div className="flex items-center gap-2">
          <RecordIdentity name={c.name} detail={<span className="ident">{c.phone}</span>} />
          {c.needsReview && <Badge tone="amber">Review</Badge>}
        </div>
      ),
    },
    {
      key: 'designation',
      header: 'Designation',
      sortable: true,
      filterable: true,
      value: (c) => c.designations[0] ?? '',
    },
    {
      key: 'experience',
      header: 'Exp.',
      sortable: true,
      align: 'right',
      width: '84px',
      className: 'whitespace-nowrap',
      value: (c) => c.totalExperience,
      render: (c) => <span className="tnum">{c.totalExperience} yr</span>,
    },
    {
      key: 'passport',
      header: 'Passport',
      filterable: true,
      width: '100px',
      value: (c) => (c.passportNo ? 'Yes' : 'No'),
      render: (c) =>
        c.passportNo ? (
          <span className="ident text-ink-600">{c.passportNo}</span>
        ) : (
          <span className="text-ink-300">No</span>
        ),
    },
    {
      key: 'source',
      header: 'Source',
      filterable: true,
      width: '110px',
      value: (c) => SOURCE_LABEL[c.source],
    },
    {
      key: 'owner',
      header: 'Assigned to',
      sortable: true,
      filterable: true,
      className: 'whitespace-nowrap',
      value: (c) => store.user(c.ownerId)?.name ?? '',
    },
    {
      key: 'order',
      header: 'Mapped order',
      filterable: true,
      className: 'whitespace-nowrap',
      value: (c) => {
        const app = store.activeApplicationFor(c.id)
        return app ? store.order(app.orderId)?.ref ?? '' : 'Not mapped'
      },
      render: (c) => {
        const app = store.activeApplicationFor(c.id)
        if (!app) return <span className="text-ink-300">Not mapped</span>
        return <span className="tnum text-ink-600">{store.order(app.orderId)?.ref}</span>
      },
    },
    {
      key: 'stage',
      header: 'Stage',
      sortable: true,
      value: (c) => store.activeApplicationFor(c.id)?.currentStage ?? 0,
      render: (c) => {
        const app = store.activeApplicationFor(c.id)
        if (!app) return <span className="text-ink-300">—</span>
        return (
          <span className="whitespace-nowrap text-ink-600">
            <span className="tnum font-medium text-ink">{app.currentStage}</span>{' '}
            {stageByNo(app.currentStage).title}
          </span>
        )
      },
    },
    {
      key: 'status',
      header: 'Status',
      filterable: true,
      width: '120px',
      value: (c) => c.status,
      render: (c) => <StatusPill status={c.status} />,
    },
    {
      key: 'added',
      header: 'Added',
      sortable: true,
      width: '110px',
      value: (c) => c.addedAt,
      render: (c) => <span className="whitespace-nowrap text-ink-500">{dateLabel(c.addedAt)}</span>,
    },
  ]

  return (
    <>
      <PageHeader
        title="Data Bank"
        eyebrow="Talent directory"
        description="Your next great placement starts here. Find talent across profiles, skills, and resumes."
        actions={
          <>
            {store.can('reassign') && selected.length > 0 && (
              <Button variant="secondary" icon={<UserCog className="h-4 w-4" />} onClick={() => setReassigning(true)}>
                Reassign {selected.length}
              </Button>
            )}
            {store.can('export') && (
              <Button variant="secondary" icon={<Download className="h-4 w-4" />}>
                Export to Excel
              </Button>
            )}
            <Button variant="primary" icon={<UserPlus className="h-4 w-4" />} onClick={() => navigate('/add-candidate')}>
              Add candidate
            </Button>
          </>
        }
      />

      <SummaryCards
        items={[
          {
            label: 'Total candidates',
            value: store.candidates.length,
            detail: 'Talent in your database',
            icon: Users,
          },
          {
            label: 'Not mapped',
            value: store.candidates.filter((candidate) => !store.activeApplicationFor(candidate.id))
              .length,
            detail: 'Available to match with an order',
            icon: UserSearch,
            tone: 'violet',
          },
          {
            label: 'Needs review',
            value: store.candidates.filter(
              (candidate) => candidate.needsReview || candidate.status === 'needs_review',
            ).length,
            detail: 'Profiles that need your attention',
            icon: FileSearch,
            tone: 'amber',
          },
          {
            label: 'Deployed',
            value: store.candidates.filter((candidate) => candidate.status === 'deployed').length,
            detail: 'Successful candidate placements',
            icon: CircleCheck,
            tone: 'green',
          },
        ]}
      />

      <Panel flush>
        <PanelHeader title="Candidate directory" meta="Search, discover, and manage your talent pool" />
        <Tabs tabs={[
          { key: 'all', label: 'Everyone', count: store.candidates.length },
          { key: 'unmapped', label: 'Not mapped' },
          { key: 'not_interested', label: 'Not interested' },
        ]} active={pool} onChange={(key) => setPool(key as typeof pool)} />
        <div className="workspace-list-toolbar">
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="Search names, skills, passports, or resumes..."
            className="min-w-0 max-w-xl flex-1"
          />
          <span className="ml-auto text-xs text-ink-400 tnum">{rows.length} candidates</span>
        </div>

        <DataTable
          rows={rows}
          columns={columns}
          rowKey={(c) => c.id}
          selectable={store.can('reassign')}
          selected={selected}
          onSelectedChange={setSelected}
          onRowClick={(c) => navigate(`/candidates/${c.id}`)}
          pageSize={20}
          empty={
            <EmptyState
              title={pool === 'not_interested' ? 'Nobody in the not-interested pool' : 'No candidates yet'}
              description={
                pool === 'not_interested'
                  ? 'Candidates who decline land here so you can offer them another order later.'
                  : 'Add a candidate, or connect an email account so resumes arrive on their own.'
              }
              action={<Button variant="primary" onClick={() => navigate('/add-candidate')}>Add candidate</Button>}
            />
          }
        />
      </Panel>

      <p className="workspace-footnote">
        Select a candidate to view their profile. Manage recruitment stages from Mapped Candidates.
      </p>

      <Dialog
        open={reassigning}
        onClose={() => setReassigning(false)}
        title={`Reassign ${selected.length} candidates`}
        description="Their open tasks move to the new owner. The 48-hour clocks keep running."
        footer={
          <>
            <Button variant="ghost" onClick={() => setReassigning(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={!reassignTo}
              onClick={() => {
                void store.reassign(selected, reassignTo)
                setSelected([])
                setReassignTo('')
                setReassigning(false)
              }}
            >
              Reassign
            </Button>
          </>
        }
      >
        <Select value={reassignTo} onChange={(e) => setReassignTo(e.target.value)} aria-label="New owner">
          <option value="">Choose a staff member</option>
          {store.users
            .filter((u) => u.role === 'staff' && u.active)
            .map((u) => (
              <option key={u.id} value={u.id}>
                {u.name}
                {u.onLeave ? ' — on leave' : ''}
              </option>
            ))}
        </Select>
      </Dialog>

    </>
  )
}
