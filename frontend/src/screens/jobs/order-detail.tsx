import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, History, Link2, Pencil, Trash2, Unlink } from 'lucide-react'
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
import { Button, CheckboxInput, Field, Input, SearchInput, Select } from '@/shared/ui/primitives'
import { Dialog, Sheet } from '@/shared/ui/overlay'
import { DataTable, type Column } from '@/shared/ui/table'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { DeleteRecordDialog } from '@/shared/components/delete-dialog'
import { normalizedSkills, skillFacets, skillKey } from '@/features/candidates/candidate-skills'
import { matchesCandidate, type CandidateMatchFilters } from '@/features/jobs/candidate-matching'
import { OrderDialog } from '@/features/jobs/components/job-dialogs'
import { RecordAudit } from '@/features/master-data/components/record-audit'

export default function OrderDetailPage() {
  const { id } = useParams()
  const store = useStore()
  const navigate = useNavigate()
  const order = store.orders.find((o) => o.id === id)

  const [matching, setMatching] = useState(false)
  const [openApp, setOpenApp] = useState<string | null>(null)
  const [unmapping, setUnmapping] = useState<string | null>(null)
  const [unmapSaving, setUnmapSaving] = useState(false)
  const [unmapError, setUnmapError] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [editing, setEditing] = useState(false)
  const [history, setHistory] = useState(false)

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
              setUnmapError('')
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
            <Button icon={<History className="h-4 w-4" />} onClick={() => setHistory(true)}>Activity</Button>
            <Button icon={<Pencil className="h-4 w-4" />} onClick={() => setEditing(true)}>Edit order</Button>
            {store.can('delete_records') && (
              <Button variant="dangerGhost" icon={<Trash2 className="h-4 w-4" />} onClick={() => setDeleting(true)}>
                Delete order
              </Button>
            )}
            <Button variant="primary" icon={<Link2 className="h-4 w-4" />} disabled={order.status !== 'open'} onClick={() => setMatching(true)}>
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
            action={<Button variant="primary" disabled={order.status !== 'open'} onClick={() => setMatching(true)}>Match candidates</Button>}
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

      {matching && <MatchDrawer key={order.id} open onClose={() => setMatching(false)} orderId={order.id} />}

      <Dialog
        open={!!unmapping}
        onClose={() => { if (!unmapSaving) setUnmapping(null) }}
        title="Unmap this candidate?"
        description="The application closes with reason Unmapped and its open tasks are cancelled. The candidate returns to the Data Bank and can be mapped to another order."
        footer={
          <>
            <Button variant="ghost" disabled={unmapSaving} onClick={() => setUnmapping(null)}>
              Keep the mapping
            </Button>
            <Button
              variant="danger"
              disabled={unmapSaving}
              onClick={async () => {
                if (!unmapping || unmapSaving) return
                setUnmapSaving(true); setUnmapError('')
                try { await store.unmap(unmapping); setUnmapping(null) }
                catch (error) { setUnmapError(error instanceof Error ? error.message : 'The candidate could not be unmapped. Please try again.') }
                finally { setUnmapSaving(false) }
              }}
            >
              {unmapSaving ? 'Unmapping…' : 'Unmap'}
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink-600">
          Stages already completed stay on the closed application as a record. Remapping starts a
          fresh application at stage 1.
        </p>
        {unmapError && <p role="alert" className="mt-3 text-sm text-stamp-700">{unmapError}</p>}
      </Dialog>

      <StageModal applicationId={openApp} open={!!openApp} onClose={() => setOpenApp(null)} />
      {editing && <OrderDialog order={order} onClose={() => setEditing(false)} />}
      {history && <RecordAudit kind="order" id={order.id} onClose={() => setHistory(false)} />}
      <DeleteRecordDialog
        kind="order"
        id={order.id}
        open={deleting}
        onClose={() => setDeleting(false)}
        onDeleted={() => navigate('/jobs')}
        onEdit={() => setEditing(true)}
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
  const [filters, setFilters] = useState<CandidateMatchFilters>({
    designation: designation.title,
    minExperience: '',
    passport: 'any',
    owner: 'all',
    location: '',
    skills: [],
    skillMode: 'all',
  })
  const [picked, setPicked] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [committed, setCommitted] = useState<string[]>([])
  const [refreshNeeded, setRefreshNeeded] = useState(false)

  const eligibleCandidates = useMemo(() => {
    const activeIds = new Set(store.applications.filter((a) => a.status === 'active').map((a) => a.candidateId))
    return store.candidates.filter((c) => !activeIds.has(c.id) && !committed.includes(c.id) && c.status !== 'deployed')
  }, [store.candidates, store.applications, committed])

  const skills = useMemo(() => skillFacets(eligibleCandidates), [eligibleCandidates])
  const designationOptions = useMemo(() => normalizedSkills([
    ...store.designations.map((d) => d.title), ...eligibleCandidates.flatMap((c) => c.designations),
  ]).sort((a, b) => a.localeCompare(b)), [store.designations, eligibleCandidates])
  const requiredSkills = normalizedSkills(designation.requiredSkills)
  const ownerIds = new Set(eligibleCandidates.map((c) => c.ownerId))
  const owners = store.users.filter((u) => u.role === 'staff' || ownerIds.has(u.id))

  const results = useMemo(() => {
    return eligibleCandidates.filter((candidate) => matchesCandidate(candidate, filters, query))
  }, [eligibleCandidates, query, filters])
  const resultIds = new Set(results.map((candidate) => candidate.id))
  const selected = picked.filter((id) => resultIds.has(id))

  const set = <K extends keyof CandidateMatchFilters>(key: K, value: CandidateMatchFilters[K]) => {
    setFilters((f) => ({ ...f, [key]: value }))
    setPicked([])
  }
  const close = () => { if (!saving) onClose() }
  const matchSelected = async () => {
    if (saving || !selected.length || order.status !== 'open') return
    setSaving(true)
    setError('')
    const failed: string[] = []
    let failureMessage = ''
    let needsRefresh = false
    for (const candidateId of selected) {
      if (!store.isCurrentSession()) return
      try {
        const result = await store.mapCandidate(candidateId, orderId)
        if (!store.isCurrentSession()) return
        if (!result) return
        setCommitted((current) => result.refreshed ? [] : [...current, candidateId])
        needsRefresh = !result.refreshed
      } catch (cause) {
        if (!store.isCurrentSession()) return
        failed.push(candidateId)
        failureMessage = cause instanceof Error ? cause.message : 'Please try again.'
      }
    }
    setPicked(failed)
    setSaving(false)
    setRefreshNeeded(needsRefresh)
    if (failed.length) {
      setError(`${selected.length - failed.length} matched; ${failed.length} could not be matched. ${failureMessage}`)
    } else if (needsRefresh) {
      setError('Candidates matched successfully. The directory could not refresh. Refresh the results to see their applications.')
    } else {
      onClose()
    }
  }

  return (
    <Sheet
      open={open}
      onClose={close}
      width="xl"
      title="Match candidates to this order"
      description={`${designation.title} · ${store.company(order.companyId)?.name} · ${order.ref}`}
      footer={
        <>
          <span className="mr-auto text-xs text-ink-500 tnum">
            {selected.length} selected · {results.length} matching of {eligibleCandidates.length} available
          </span>
          <Button variant="ghost" onClick={close} disabled={saving}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={selected.length === 0 || saving || order.status !== 'open'}
            onClick={() => void matchSelected()}
          >
            {saving ? 'Matching candidates…' : `Match ${selected.length || ''} and start stage 1`}
          </Button>
        </>
      }
    >
      {refreshNeeded && <div className="px-5 pt-3"><Button disabled={saving} onClick={async () => {
        setSaving(true)
        try {
          await store.refresh({ keepCurrent: true })
          if (store.isCurrentSession()) { setCommitted([]); setRefreshNeeded(false); setError('') }
        } catch (cause) {
          if (store.isCurrentSession()) setError(cause instanceof Error ? cause.message : 'The results could not refresh. Please retry.')
        } finally { setSaving(false) }
      }}>Refresh matching results</Button></div>}
      <fieldset disabled={saving} className="min-w-0 border-b border-rule bg-white px-5 py-3">
        <SearchInput value={query} onChange={(value) => { setQuery(value); setPicked([]) }} placeholder="Name, contact, passport, skill, or resume text" />
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Designation">
          <Select value={filters.designation} onChange={(e) => set('designation', e.target.value)}>
            <option value="">Any designation</option>
            {designationOptions.map((title) => (
              <option key={skillKey(title)} value={title}>
                {title}
              </option>
            ))}
          </Select>
          </Field>
          <Field label="Minimum experience (years)">
            <Input type="number" min="0" step="0.5" value={filters.minExperience} onChange={(e) => set('minExperience', e.target.value)} placeholder="Any experience" />
          </Field>
          <Field label="Current location">
            <Input value={filters.location} onChange={(e) => set('location', e.target.value)} placeholder="City or location" />
          </Field>
          <Field label="Assigned staff">
          <Select value={filters.owner} onChange={(e) => set('owner', e.target.value)}>
            <option value="all">Any staff</option>
            {owners.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name}
                </option>
              ))}
          </Select>
          </Field>
          <Field label="Passport details">
            <Select value={filters.passport} onChange={(e) => set('passport', e.target.value as CandidateMatchFilters['passport'])}>
              <option value="any">Any passport status</option>
              <option value="present">Passport number recorded</option>
              <option value="missing">Passport number missing</option>
            </Select>
          </Field>
          <Field label="Recorded skills">
            <Select value="" onChange={(e) => {
              if (e.target.value) set('skills', normalizedSkills([...filters.skills, e.target.value]))
            }}>
              <option value="">Add a skill filter</option>
              {skills.filter((skill) => !filters.skills.some((value) => skillKey(value) === skill.key)).map((skill) => (
                <option key={skill.key} value={skill.label}>{skill.label} ({skill.count})</option>
              ))}
            </Select>
          </Field>
        </div>
        {filters.skills.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Select className="w-auto" value={filters.skillMode} onChange={(e) => set('skillMode', e.target.value as 'all' | 'any')} aria-label="Skill match rule">
              <option value="all">All selected skills</option>
              <option value="any">Any selected skill</option>
            </Select>
            {filters.skills.map((skill) => (
              <button key={skillKey(skill)} type="button" className="rounded-full border border-navy-100 bg-navy-50 px-2.5 py-1 text-xs text-navy-700" aria-label={`Remove ${skill} filter`} onClick={() => set('skills', filters.skills.filter((value) => skillKey(value) !== skillKey(skill)))}>
                {skill} <span aria-hidden="true">×</span>
              </button>
            ))}
          </div>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {(requiredSkills.length > 0 || designation.minExperience > 0) && (
            <Button size="sm" onClick={() => {
              setFilters((f) => ({ ...f, designation: designation.title, minExperience: designation.minExperience > 0 ? String(designation.minExperience) : '', skills: requiredSkills, skillMode: 'all' }))
              setPicked([])
            }}>Use designation requirements</Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => {
            setFilters({ designation: '', minExperience: '', passport: 'any', owner: 'all', location: '', skills: [], skillMode: 'all' })
            setQuery('')
            setPicked([])
          }}>Clear filters</Button>
          <p className="text-micro text-ink-400">Skill filters use the skills recorded on each profile.</p>
        </div>
      </fieldset>
      {order.status !== 'open' && <p role="status" className="border-b border-rule bg-amber-50 px-5 py-3 text-sm text-amber-700">This order is {order.status.replace('_', ' ')}. Open it before matching candidates.</p>}
      {error && <p role="alert" className="border-b border-rule bg-stamp-50 px-5 py-3 text-sm text-stamp-700">{error}</p>}

      {results.length === 0 ? (
        <EmptyState
          title="No candidates match these filters"
          description="Adjust or clear the filters. Candidates already on another order and deployed candidates are hidden."
        />
      ) : (
        <ul className="divide-y divide-rule-soft">
          {results.map((c) => (
            <MatchRow
              key={c.id}
              candidate={c}
              checked={selected.includes(c.id)}
              disabled={saving || order.status !== 'open'}
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
  disabled,
  onToggle,
}: {
  candidate: Candidate
  checked: boolean
  disabled: boolean
  onToggle: () => void
}) {
  const store = useStore()
  return (
    <li className={cn('px-5 py-3 transition-colors', checked ? 'bg-navy-50/70' : 'hover:bg-field/60')}>
      <label className="flex cursor-pointer items-center gap-3">
        <CheckboxInput checked={checked} onChange={onToggle} disabled={disabled} aria-label={`Select ${candidate.name}`} />
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
          {normalizedSkills(candidate.skills).slice(0, 2).map((s) => (
            <Badge key={skillKey(s)}>{s}</Badge>
          ))}
        </div>
      </label>
    </li>
  )
}
