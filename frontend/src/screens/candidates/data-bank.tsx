import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CircleCheck, Download, FileSearch, UserCog, UserPlus, Users, UserSearch } from 'lucide-react'
import { dateLabel } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { api, fileHref } from '@/shared/api/client'
import { stageByNo } from '@/shared/workflow/stages'
import type { Candidate } from '@/shared/model/types'
import { DataTable, type Column } from '@/shared/ui/table'
import { Badge, EmptyState, PageHeader, Panel, PanelHeader, StatusPill } from '@/shared/ui/display'
import { RecordIdentity, SummaryCards } from '@/shared/ui/workspace'
import { Button, Field, SearchInput, Select, Textarea } from '@/shared/ui/primitives'
import { Dialog, Tabs } from '@/shared/ui/overlay'
import { normalizedSkills, skillFacets } from '@/features/candidates/candidate-skills'

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
  const [reassignReason, setReassignReason] = useState('')
  const [skills, setSkills] = useState<string[]>([])
  const [skillMode, setSkillMode] = useState<'all' | 'any'>('all')
  const [skillStatus, setSkillStatus] = useState('all')
  const [page, setPage] = useState(0)
  const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' }>({ key: 'added', dir: 'desc' })
  const [columnFilters, setColumnFilters] = useState<Record<string, string>>({})
  const [result, setResult] = useState<{ items: Candidate[]; total: number; page: number } | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [exporting, setExporting] = useState(false)
  const [mutationError, setMutationError] = useState('')
  const [reassigningBusy, setReassigningBusy] = useState(false)

  const poolCandidates = useMemo(() => store.candidates.filter((candidate) => (
    pool === 'not_interested' ? candidate.status === 'not_interested'
      : pool === 'unmapped' ? !store.activeApplicationFor(candidate.id) : true
  )), [store.candidates, store.applications, pool])
  const facets = useMemo(() => skillFacets(poolCandidates), [poolCandidates])
  const hasFilters = Boolean(query.trim() || skills.length || skillStatus !== 'all' || Object.values(columnFilters).some(Boolean))
  const resetFilters = () => { setQuery(''); setSkills([]); setSkillStatus('all'); setSkillMode('all'); setColumnFilters({}) }

  const params = new URLSearchParams({ q: query, pool, skill_mode: skillMode, missing_skills: String(skillStatus === 'missing'), column_filters: JSON.stringify(columnFilters), sort: sort.key, direction: sort.dir })
  skills.forEach(skill => params.append('skills', skill))
  const filterQuery = params.toString()
  useEffect(() => { setPage(0); setSelected([]) }, [filterQuery])
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setLoadError('')
    const timer = window.setTimeout(() => {
      api.get<{ items: Candidate[]; total: number; page: number }>(`/candidates?${filterQuery}&page=${page}&page_size=20`, { signal: controller.signal })
        .then(data => { if (!controller.signal.aborted) setResult(data) })
        .catch(error => { if (!controller.signal.aborted) setLoadError(error instanceof Error ? error.message : 'Search failed.') })
        .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    }, 250)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [filterQuery, page, store.candidates, store.me.id])
  const rows = result?.items ?? []
  const exportExcel = async () => {
    setExporting(true); setMutationError('')
    try {
      const response = await fetch(fileHref(`/api/candidates/export?${filterQuery}`), { credentials: 'include' })
      if (!response.ok) throw new Error('Unable to export this filtered Data Bank. Please retry.')
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a'); link.href = url; link.download = 'candidate-data-bank.xlsx'
      document.body.appendChild(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (error) { setMutationError(error instanceof Error ? error.message : 'Export failed.') } finally { setExporting(false) }
  }

  // A hidden selection must not be reassigned after changing the talent pool.
  useEffect(() => { setSelected([]) }, [query, pool, skills, skillMode, skillStatus, page])

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
    { key: 'phone', header: 'Phone', value: (c) => c.phone },
    {
      key: 'designation',
      header: 'Designation',
      sortable: true,
      filterable: true,
      value: (c) => c.designations[0] ?? '',
    },
    {
      key: 'skills',
      header: 'Skills',
      value: (c) => normalizedSkills(c.skills).join(', '),
      render: (c) => normalizedSkills(c.skills).length ? (
        <div className="flex min-w-40 max-w-64 flex-wrap gap-1">
          {normalizedSkills(c.skills).map((skill) => <Badge key={skill}>{skill}</Badge>)}
        </div>
      ) : <span className="text-ink-400">Not recorded</span>,
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
              <Button variant="secondary" disabled={loading || !!loadError} icon={<UserCog className="h-4 w-4" />} onClick={() => { setMutationError(''); setReassigning(true) }}>
                Reassign {selected.length}
              </Button>
            )}
            {store.can('export') && (
              <Button variant="secondary" disabled={loading || !!loadError || exporting} icon={<Download className="h-4 w-4" />} onClick={() => void exportExcel()}>
                {exporting ? 'Preparing Excel…' : 'Export to Excel'}
              </Button>
            )}
            <Button variant="primary" icon={<UserPlus className="h-4 w-4" />} onClick={() => navigate('/add-candidate')}>
              Add candidate
            </Button>
          </>
        }
      />
      {(loadError || mutationError) && <p role="alert" className="mb-4 rounded-panel bg-stamp-50 p-4 text-sm text-stamp-600">{loadError || mutationError}{loadError && result && ' Showing the last loaded page.'}</p>}

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
          <span className="ml-auto text-xs text-ink-400 tnum" role="status">{loading ? 'Searching…' : `${result?.total ?? 0} candidates`}</span>
        </div>

        <div className="border-b border-rule px-4 pb-4" aria-label="Skill filters">
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Filter by recorded skill" hint="Counts show candidates in the selected pool.">
              <Select value="" onChange={(event) => {
                if (event.target.value) { setSkills((current) => [...current, event.target.value]); setSkillStatus('all') }
              }}>
                <option value="">Choose a skill</option>
                {facets.filter((facet) => !skills.includes(facet.key)).map((facet) => (
                  <option key={facet.key} value={facet.key}>{facet.label} ({facet.count})</option>
                ))}
              </Select>
            </Field>
            <Field label="Skill matching">
              <Select value={skillMode} onChange={(event) => setSkillMode(event.target.value as 'all' | 'any')}>
                <option value="all">All selected skills</option>
                <option value="any">Any selected skill</option>
              </Select>
            </Field>
            <Field label="Skill coverage">
              <Select value={skillStatus} onChange={(event) => {
                setSkillStatus(event.target.value)
                if (event.target.value === 'missing') setSkills([])
              }}>
                <option value="all">All candidates</option>
                <option value="missing">No skills recorded</option>
              </Select>
            </Field>
          </div>
          {(skills.length > 0 || hasFilters) && <div className="mt-3 flex flex-wrap items-center gap-2">
            {skills.map((key) => <Button key={key} size="sm" onClick={() => setSkills((current) => current.filter((skill) => skill !== key))}
              aria-label={`Remove skill ${facets.find((facet) => facet.key === key)?.label ?? key}`}>
              {facets.find((facet) => facet.key === key)?.label ?? key} ×
            </Button>)}
            <Button size="sm" variant="ghost" onClick={resetFilters}>Clear filters</Button>
          </div>}
        </div>

        <DataTable
          resetPageKey={`${pool}:${query}:${skills.join('|')}:${skillMode}:${skillStatus}`}
          rows={rows}
          columns={columns.map(column => ({ ...column, filterable: true, sortable: true }))}
          rowKey={(c) => c.id}
          selectable={store.can('reassign')}
          selected={selected}
          onSelectedChange={setSelected}
          onRowClick={(c) => navigate(`/candidates/${c.id}`)}
          pageSize={20}
          server={{ page: result?.page ?? page, total: result?.total ?? 0, onPageChange: setPage, sort,
            onSortChange: next => setSort(next), filters: columnFilters,
            onFilterChange: (key, value) => setColumnFilters(previous => ({ ...previous, [key]: value })) }}
          empty={
            <EmptyState
              title={loading ? 'Searching candidates…' : hasFilters ? 'No matching candidates' : pool === 'not_interested' ? 'Nobody in the not-interested pool' : 'No candidates yet'}
              description={
                hasFilters ? 'Try another skill, switch to any selected skill, or clear your filters.' : pool === 'not_interested'
                  ? 'Candidates who decline land here so you can offer them another order later.'
                  : 'Add a candidate, or connect an email account so resumes arrive on their own.'
              }
              action={hasFilters ? <Button onClick={resetFilters}>Clear filters</Button> : <Button variant="primary" onClick={() => navigate('/add-candidate')}>Add candidate</Button>}
            />
          }
        />
      </Panel>

      <p className="workspace-footnote">
        Select a candidate to view their profile. Manage recruitment stages from Mapped Candidates.
      </p>

      <Dialog
        open={reassigning}
        onClose={() => { if (!reassigningBusy) setReassigning(false) }}
        title={`Reassign ${selected.length} candidates`}
        description="Their open tasks move to the new owner. The 48-hour clocks keep running."
        footer={
          <>
            <Button variant="ghost" disabled={reassigningBusy} onClick={() => setReassigning(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={!reassignTo || !reassignReason.trim() || reassigningBusy}
              onClick={async () => {
                setReassigningBusy(true); setMutationError('')
                try { await store.reassign(selected.filter(id => rows.some(row => row.id === id)), reassignTo, reassignReason.trim()); setSelected([]); setReassignTo(''); setReassignReason(''); setReassigning(false) }
                catch (error) { setMutationError(error instanceof Error ? error.message : 'Reassignment failed.') }
                finally { setReassigningBusy(false) }
              }}
            >
              {reassigningBusy ? 'Reassigning…' : 'Reassign'}
            </Button>
          </>
        }
      >
        <Select value={reassignTo} onChange={(e) => setReassignTo(e.target.value)} aria-label="New owner">
          <option value="">Choose a staff member</option>
          {store.users
            .filter((u) => u.role === 'staff' && u.active && !u.onLeave)
            .map((u) => (
              <option key={u.id} value={u.id}>
                {u.name}
                {u.onLeave ? ' — on leave' : ''}
              </option>
            ))}
        </Select>
        <Field label="Reason for reassignment" required className="mt-4">
          <Textarea value={reassignReason} disabled={reassigningBusy} maxLength={1000} onChange={(event) => setReassignReason(event.target.value)} placeholder="Explain the ownership change for the candidate history" />
        </Field>
        {mutationError && <p role="alert" className="mt-3 text-sm text-stamp-600">{mutationError}</p>}
      </Dialog>

    </>
  )
}
