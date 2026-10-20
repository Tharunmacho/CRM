import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, Link2, Trash2, Unlink } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { dateLabel, dateTimeLabel, money } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { closureLabels, stageByNo } from '@/shared/workflow/stages'
import type { Application, Candidate } from '@/shared/model/types'
import {
  Badge,
  Def,
  DefList,
  EmptyState,
  PageHeader,
  Panel,
  PanelHeader,
  StageChip,
  StageDot,
  StatusPill,
} from '@/shared/ui/display'
import { Button, Checkbox, SearchInput, Select } from '@/shared/ui/primitives'
import { Dialog, Sheet } from '@/shared/ui/overlay'
import { DataTable, type Column } from '@/shared/ui/table'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { DeleteRecordDialog } from '@/shared/components/delete-dialog'

export default function OrderDetailPage() {
  const { id } = useParams()
  const store = useStore()
  const navigate = useNavigate()
  const order = store.orders.find((o) => o.id === id)

  const [matching, setMatching] = useState(false)
  const [openApp, setOpenApp] = useState<string | null>(null)
  const [unmapping, setUnmapping] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  if (!order) {
    return (
      <EmptyState
        title="That order does not exist"
        description="It may have been closed and removed."
        action={<Button onClick={() => navigate('/jobs')}>Back to Jobs</Button>}
      />
    )
  }

  const client = store.company(order.companyId)!
  const designation = store.designation(order.designationId)!
  const apps = store.applications.filter((a) => a.orderId === order.id)
  const activeApps = apps.filter((a) => a.status === 'active')
  const deployed = apps.filter((a) => a.status === 'deployed')
  const selected = activeApps.filter((a) => a.currentStage > 4)

  const matchedColumns: Column<Application>[] = [
    {
      key: 'candidate',
      header: 'Candidate',
      sortable: true,
      width: '26%',
      value: (a) => store.candidate(a.candidateId)?.name ?? '',
      render: (a) => {
        const c = store.candidate(a.candidateId)!
        return (
          <div className="min-w-0">
            <p className="truncate font-semibold text-ink">{c.name}</p>
            <p className="truncate text-micro text-ink-400 ident">{c.phone}</p>
          </div>
        )
      },
    },
    {
      key: 'stage',
      header: 'Current stage',
      sortable: true,
      filterable: true,
      value: (a) =>
        a.status === 'closed' && a.closureReason
          ? closureLabels[a.closureReason]
          : `${a.currentStage}. ${stageByNo(a.currentStage).title}`,
      render: (a) =>
        a.status === 'closed' && a.closureReason ? (
          <span className="text-stamp-700">{closureLabels[a.closureReason]}</span>
        ) : (
          <span className="flex items-center gap-2 whitespace-nowrap">
            <StageDot no={a.currentStage} />
            <span className="text-ink-700">{stageByNo(a.currentStage).title}</span>
          </span>
        ),
    },
    {
      key: 'owner',
      header: 'Owner',
      sortable: true,
      filterable: true,
      className: 'whitespace-nowrap',
      value: (a) => store.user(store.candidate(a.candidateId)?.ownerId ?? '')?.name ?? '',
    },
    {
      key: 'updated',
      header: 'Last update',
      sortable: true,
      className: 'whitespace-nowrap',
      value: (a) => a.updatedAt,
      render: (a) => <span className="text-ink-500">{dateTimeLabel(a.updatedAt)}</span>,
    },
    {
      key: 'status',
      header: 'Status',
      filterable: true,
      value: (a) => a.status,
      render: (a) => <StatusPill status={a.status} />,
    },
    {
      key: 'unmap',
      header: '',
      align: 'right',
      render: (a) =>
        a.status === 'active' && store.can('map_candidates') ? (
          <button
            onClick={(e) => {
              e.stopPropagation()
              setUnmapping(a.id)
            }}
            aria-label={`Unmap ${store.candidate(a.candidateId)?.name}`}
            title="Unmap"
            className="rounded-control p-1.5 text-ink-400 hover:bg-stamp-50 hover:text-stamp-600"
          >
            <Unlink className="h-3.5 w-3.5" />
          </button>
        ) : null,
    },
  ]

  return (
    <>
      <Link to="/jobs" className="mb-3 inline-flex items-center gap-1.5 text-xs text-ink-500 hover:text-ink">
        <ArrowLeft className="h-3.5 w-3.5" />
        Jobs / Positions
      </Link>

      <PageHeader
        title={designation.title}
        description={`${client.name} · ${order.city}, ${order.country} · ${order.ref}`}
        actions={
          <>
            {store.can('delete_records') && (
              <Button variant="dangerGhost" icon={<Trash2 className="h-4 w-4" />} onClick={() => setDeleting(true)}>
                Delete order
              </Button>
            )}
            <Button variant="primary" icon={<Link2 className="h-4 w-4" />} onClick={() => setMatching(true)}>
              Match candidates
            </Button>
          </>
        }
      />

      <div className="grid gap-4 lg:grid-cols-4">
        <Panel className="min-w-0 lg:col-span-3">
          <DefList cols={3}>
            <Def label="Status">
              <StatusPill status={order.status} />
            </Def>
            <Def label="Openings">
              <span className="tnum">{order.openings}</span>
            </Def>
            <Def label="Salary">{money(order.salary, order.currency)}</Def>
            <Def label="Contract period">{order.contractPeriod}</Def>
            <Def label="Interview date">{dateLabel(order.interviewDate)}</Def>
            <Def label="Order deadline">{dateLabel(order.deadline)}</Def>
          </DefList>
          <div className="mt-3.5 flex flex-wrap gap-1.5">
            {order.benefits.map((b) => (
              <Badge key={b}>{b}</Badge>
            ))}
          </div>
        </Panel>

        <Panel>
          <p className="text-xs text-ink-500">Progress</p>
          <p className="mt-2 text-stat font-semibold text-ink tnum">
            {deployed.length}
            <span className="text-lead font-normal text-ink-400">/{order.openings}</span>
          </p>
          <p className="text-micro text-ink-400">deployed</p>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-field">
            <div
              className="h-full rounded-full bg-navy-600"
              style={{ width: `${Math.min(100, (deployed.length / order.openings) * 100)}%` }}
            />
          </div>
          <dl className="mt-3 space-y-1.5 border-t border-rule-soft pt-3 text-xs">
            <div className="flex justify-between">
              <dt className="text-ink-500">Matched</dt>
              <dd className="tnum font-medium text-ink">{activeApps.length}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-ink-500">Selected</dt>
              <dd className="tnum font-medium text-ink">{selected.length}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-ink-500">Closed</dt>
              <dd className="tnum font-medium text-ink">{apps.filter((a) => a.status === 'closed').length}</dd>
            </div>
          </dl>
        </Panel>
      </div>

      <Panel flush className="mt-4">
        <PanelHeader title="Matched candidates" meta={`${apps.length} applications on this order`} />
        {apps.length === 0 ? (
          <EmptyState
            title="No candidates matched yet"
            description="Search the Data Bank and match candidates. Matching is what unlocks their stage modal."
            action={<Button variant="primary" onClick={() => setMatching(true)}>Match candidates</Button>}
          />
        ) : (
          <DataTable
            rows={apps}
            columns={matchedColumns}
            rowKey={(a) => a.id}
            onRowClick={(a) => setOpenApp(a.id)}
            pageSize={15}
          />
        )}
      </Panel>

      <MatchDrawer open={matching} onClose={() => setMatching(false)} orderId={order.id} />

      <Dialog
        open={!!unmapping}
        onClose={() => setUnmapping(null)}
        title="Unmap this candidate?"
        description="The application closes with reason Unmapped and its open tasks are cancelled. The candidate returns to the Data Bank and can be mapped to another order."
        footer={
          <>
            <Button variant="ghost" onClick={() => setUnmapping(null)}>
              Keep the mapping
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                void store.unmap(unmapping!)
                setUnmapping(null)
              }}
            >
              Unmap
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink-600">
          Stages already completed stay on the closed application as a record. Remapping starts a
          fresh application at stage 1.
        </p>
      </Dialog>

      <StageModal applicationId={openApp} open={!!openApp} onClose={() => setOpenApp(null)} />
      <DeleteRecordDialog
        kind="order"
        id={order.id}
        open={deleting}
        onClose={() => setDeleting(false)}
        onDeleted={() => navigate('/jobs')}
      />
    </>
  )
}

/* ------------------------------------------------------------ Match drawer */

function MatchDrawer({
  open,
  onClose,
  orderId,
}: {
  open: boolean
  onClose: () => void
  orderId: string
}) {
  const store = useStore()
  const order = store.order(orderId)!
  const designation = store.designation(order.designationId)!

  const [query, setQuery] = useState('')
  const [filters, setFilters] = useState({
    designation: designation.title,
    minExperience: '',
    passportOnly: false,
    owner: 'all',
    city: '',
  })
  const [picked, setPicked] = useState<string[]>([])

  const results = useMemo(() => {
    const q = query.trim().toLowerCase()
    return store.candidates.filter((c) => {
      if (store.activeApplicationFor(c.id)) return false
      if (c.status === 'deployed') return false
      if (filters.designation && !c.designations.includes(filters.designation)) return false
      if (filters.minExperience && c.totalExperience < Number(filters.minExperience)) return false
      if (filters.passportOnly && !c.passportNo) return false
      if (filters.owner !== 'all' && c.ownerId !== filters.owner) return false
      if (filters.city && !c.currentCity.toLowerCase().includes(filters.city.toLowerCase())) return false
      if (!q) return true
      return (
        c.name.toLowerCase().includes(q) ||
        c.skills.join(' ').toLowerCase().includes(q) ||
        c.resumeText.toLowerCase().includes(q)
      )
    })
  }, [store.candidates, store.applications, query, filters])

  const set = (k: string, v: unknown) => setFilters((f) => ({ ...f, [k]: v }))

  return (
    <Sheet
      open={open}
      onClose={onClose}
      width="xl"
      title="Match candidates to this order"
      description={`${designation.title} · ${store.company(order.companyId)?.name} · ${order.ref}`}
      footer={
        <>
          <span className="mr-auto text-xs text-ink-500 tnum">
            {picked.length} selected · {results.length} available
          </span>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={picked.length === 0}
            onClick={() => {
              picked.forEach((candidateId) => void store.mapCandidate(candidateId, orderId))
              setPicked([])
              onClose()
            }}
          >
            Match {picked.length || ''} and start stage 1
          </Button>
        </>
      }
    >
      <div className="sticky top-0 z-10 border-b border-rule bg-white px-5 py-3">
        <SearchInput value={query} onChange={setQuery} placeholder="Name, skill, or resume text" />
        <div className="mt-2.5 grid gap-2 sm:grid-cols-4">
          <Select value={filters.designation} onChange={(e) => set('designation', e.target.value)} aria-label="Designation">
            <option value="">Any designation</option>
            {store.designations.map((d) => (
              <option key={d.id} value={d.title}>
                {d.title}
              </option>
            ))}
          </Select>
          <Select value={filters.minExperience} onChange={(e) => set('minExperience', e.target.value)} aria-label="Minimum experience">
            <option value="">Any experience</option>
            {[2, 3, 5, 8, 10].map((n) => (
              <option key={n} value={n}>
                {n}+ years
              </option>
            ))}
          </Select>
          <Select value={filters.owner} onChange={(e) => set('owner', e.target.value)} aria-label="Assigned staff">
            <option value="all">Any staff</option>
            {store.users
              .filter((u) => u.role === 'staff')
              .map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name}
                </option>
              ))}
          </Select>
          <div className="flex items-center">
            <Checkbox
              checked={filters.passportOnly}
              onChange={(v) => set('passportOnly', v)}
              label="Passport on file"
            />
          </div>
        </div>
      </div>

      {results.length === 0 ? (
        <EmptyState
          title="No candidates match these filters"
          description="Widen the designation or experience filter. Candidates already on another order are hidden."
        />
      ) : (
        <ul className="divide-y divide-rule-soft">
          {results.map((c) => (
            <MatchRow
              key={c.id}
              candidate={c}
              checked={picked.includes(c.id)}
              onToggle={() =>
                setPicked((p) => (p.includes(c.id) ? p.filter((x) => x !== c.id) : [...p, c.id]))
              }
            />
          ))}
        </ul>
      )}
    </Sheet>
  )
}

function MatchRow({
  candidate,
  checked,
  onToggle,
}: {
  candidate: Candidate
  checked: boolean
  onToggle: () => void
}) {
  const store = useStore()
  return (
    <li className={cn('px-5 py-3 transition-colors', checked ? 'bg-navy-50/70' : 'hover:bg-field/60')}>
      <label className="flex cursor-pointer items-center gap-3">
        <Checkbox checked={checked} onChange={onToggle} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate text-sm font-medium text-ink">{candidate.name}</span>
            {candidate.gulfExperience > 0 && <Badge tone="navy">Gulf returnee</Badge>}
            {!candidate.passportNo && <Badge tone="amber">No passport</Badge>}
            {candidate.status === 'not_interested' && <Badge tone="amber">Declined before</Badge>}
          </div>
          <p className="mt-0.5 truncate text-micro text-ink-400">
            {candidate.designations.join(', ')} · {candidate.totalExperience} yr ·{' '}
            {candidate.currentCity} · {store.user(candidate.ownerId)?.name}
          </p>
        </div>
        <div className="hidden shrink-0 gap-1 sm:flex">
          {candidate.skills.slice(0, 2).map((s) => (
            <Badge key={s}>{s}</Badge>
          ))}
        </div>
      </label>
    </li>
  )
}
