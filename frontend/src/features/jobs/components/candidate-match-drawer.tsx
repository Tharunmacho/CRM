import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ExternalLink, RefreshCw } from 'lucide-react'
import { api } from '@/shared/api/client'
import { cn } from '@/shared/lib/cn'
import type { Candidate } from '@/shared/model/types'
import { useStore } from '@/shared/state/crm-store'
import { Badge, EmptyState } from '@/shared/ui/display'
import { Button, CheckboxInput, Field, Input, SearchInput, Select } from '@/shared/ui/primitives'
import { Sheet } from '@/shared/ui/overlay'
import { normalizedSkills, skillKey } from '@/features/candidates/candidate-skills'
import { useSkillFacets } from '@/features/candidates/use-skill-facets'
import type { CandidateMatchFilters } from '@/features/jobs/candidate-matching'

interface MatchCandidate extends Candidate {
  matchEvidence: { matchedSkills: string[]; missingSkills: string[]; meetsExperience: boolean }
}
interface MatchResult {
  items: MatchCandidate[]
  total: number
  page: number
  pageSize: number
  requirements: { title: string; skills: string[]; minExperience: number; orderStatus: string }
}
const emptyFilters: CandidateMatchFilters = { designation: '', minExperience: '', passport: 'any', owner: 'all', location: '', skills: [], skillMode: 'all' }

export function CandidateMatchDrawer({ onClose, orderId }: { onClose: () => void; orderId: string }) {
  const store = useStore()
  const order = store.order(orderId)!
  const designation = store.designation(order.designationId)!
  const [query, setQuery] = useState('')
  const [filters, setFilters] = useState<CandidateMatchFilters>({ ...emptyFilters, designation: designation.title })
  const [picked, setPicked] = useState<string[]>([])
  const [page, setPage] = useState(0)
  const [revision, setRevision] = useState(0)
  const [snapshot, setSnapshot] = useState<{ key: string; data: MatchResult } | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [refreshNeeded, setRefreshNeeded] = useState(false)
  const coverage = useSkillFacets('matchable=true', revision)
  const params = new URLSearchParams({ order_id: orderId, q: query, designation: filters.designation,
    passport: filters.passport, location: filters.location, skill_mode: filters.skillMode, sort: 'name', direction: 'asc', page: String(page), page_size: '20' })
  if (filters.owner !== 'all') params.set('staff', filters.owner)
  if (filters.minExperience !== '') params.set('min_experience', filters.minExperience)
  filters.skills.forEach(skill => params.append('skills', skill))
  const requestKey = params.toString()
  const snapshotKey = `${requestKey}:${revision}`
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setLoadError('')
    const timer = window.setTimeout(() => {
      void api.get<MatchResult>(`/candidates/matching?${requestKey}`, { signal: controller.signal })
        .then(data => { if (!controller.signal.aborted) setSnapshot({ key: snapshotKey, data }) })
        .catch(cause => { if (!controller.signal.aborted) setLoadError(cause instanceof Error ? cause.message : 'Matching candidates could not load.') })
        .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    }, 250)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [requestKey, snapshotKey, store.me.id])
  const result = snapshot?.key === snapshotKey ? snapshot.data : null
  const ready = !!result && !loading && !loadError
  const requirements = result?.requirements ?? snapshot?.data.requirements
  const orderOpen = (requirements?.orderStatus ?? order.status) === 'open'
  const selected = ready ? picked.filter(id => result.items.some(candidate => candidate.id === id)) : []
  const facets = coverage.data?.skills ?? []
  const resetSelection = () => { setPicked([]); setPage(0) }
  const set = <K extends keyof CandidateMatchFilters>(key: K, value: CandidateMatchFilters[K]) => {
    setFilters(current => ({ ...current, [key]: value })); resetSelection()
  }
  const refresh = async () => {
    if (saving) return
    setSaving(true); setError(''); setPicked([])
    try { await store.refresh({ keepCurrent: true }); setRefreshNeeded(false) }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'The directory could not refresh.') }
    finally { setRevision(value => value + 1); setSaving(false) }
  }
  const matchSelected = async () => {
    if (saving || !selected.length || !orderOpen) return
    setSaving(true); setError('')
    const failed: string[] = []
    let failureMessage = '', needsRefresh = false
    for (const candidateId of selected) {
      if (!store.isCurrentSession()) return
      try {
        const committed = await store.mapCandidate(candidateId, orderId)
        if (!store.isCurrentSession() || !committed) return
        needsRefresh = !committed.refreshed
      } catch (cause) {
        if (!store.isCurrentSession()) return
        failed.push(candidateId)
        failureMessage = cause instanceof Error ? cause.message : 'Please try again.'
      }
    }
    setPicked(failed); setSaving(false); setRefreshNeeded(needsRefresh)
    setRevision(value => value + 1)
    if (failed.length) setError(`${selected.length - failed.length} matched; ${failed.length} could not be matched. ${failureMessage}`)
    else if (needsRefresh) setError('Candidates matched successfully. Refresh the directory to see their applications.')
    else onClose()
  }

  return <Sheet open onClose={() => { if (!saving) onClose() }} width="xl" title="Match candidates to this order"
    description={`${designation.title} · ${store.company(order.companyId)?.name} · ${order.ref}`}
    footer={<div className="flex w-full flex-wrap items-center justify-end gap-2">
      <p className="mr-auto w-full text-sm text-ink-500 sm:w-auto">{selected.length} selected · {ready ? result.total : '…'} matching candidates</p>
      <Button variant="ghost" disabled={saving} onClick={onClose}>Cancel</Button>
      <Button variant="primary" disabled={!selected.length || saving || !ready || !orderOpen} onClick={() => void matchSelected()}>{saving ? 'Please wait…' : `Match ${selected.length || ''} and start stage 1`}</Button>
    </div>}>
    <section className="border-b border-rule bg-field/50 p-5" aria-label="Order requirements">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h3 className="font-semibold text-ink">What this order needs</h3><p className="mt-1 text-sm text-ink-500">{requirements?.title ?? designation.title} · {requirements?.minExperience ?? designation.minExperience} years minimum experience</p></div>
        <Button size="sm" icon={<RefreshCw className="h-4 w-4" />} disabled={saving} onClick={() => void refresh()}>Refresh results</Button>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">{(requirements?.skills ?? designation.requiredSkills).map(skill => <Badge key={skillKey(skill)} tone="navy">{skill}</Badge>)}</div>
      <p className="mt-3 text-sm text-ink-500">Compare the saved skills below. Missing skills mean they are not recorded on the profile; review the candidate before making a decision.</p>
    </section>
    <fieldset disabled={saving} className="min-w-0 border-b border-rule p-5">
      <SearchInput value={query} onChange={value => { setQuery(value); resetSelection() }} placeholder="Search names, contact details, skills or resume text" />
      <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Field label="Designation" hint="Exact recorded designation, or leave blank."><Input list="matching-designations" value={filters.designation} onChange={event => set('designation', event.target.value)} placeholder="Any designation" /><datalist id="matching-designations">{normalizedSkills(store.designations.map(item => item.title)).map(title => <option key={title} value={title} />)}</datalist></Field>
        <Field label="Minimum experience (years)"><Input type="number" min="0" max="100" step="0.5" value={filters.minExperience} onChange={event => set('minExperience', event.target.value)} placeholder="Any experience" /></Field>
        <Field label="Current location"><Input value={filters.location} onChange={event => set('location', event.target.value)} placeholder="City or location" /></Field>
        <Field label="Assigned staff"><Select value={filters.owner} onChange={event => set('owner', event.target.value)}><option value="all">Any staff</option>{store.users.filter(user => user.active).map(user => <option key={user.id} value={user.id}>{user.name}</option>)}</Select></Field>
        <Field label="Passport details"><Select value={filters.passport} onChange={event => set('passport', event.target.value as CandidateMatchFilters['passport'])}><option value="any">Any passport status</option><option value="present">Passport number recorded</option><option value="missing">Passport number missing</option></Select></Field>
        <Field label="Recorded skills"><Select value="" disabled={coverage.loading || !!coverage.error} onChange={event => { if (event.target.value) set('skills', normalizedSkills([...filters.skills, event.target.value])) }}><option value="">Add a skill filter</option>{facets.filter(facet => !filters.skills.some(skill => skillKey(skill) === facet.key)).map(facet => <option key={facet.key} value={facet.label}>{facet.label} ({facet.count})</option>)}</Select></Field>
      </div>
      {coverage.error && <p role="alert" className="mt-3 text-sm text-stamp-700">{coverage.error} <button type="button" className="underline" onClick={coverage.refresh}>Retry skill groups</button></p>}
      {filters.skills.length > 0 && <div className="mt-4 flex flex-wrap gap-2">
        <Select className="w-full sm:w-auto" aria-label="Skill match rule" value={filters.skillMode} onChange={event => set('skillMode', event.target.value as 'all' | 'any')}><option value="all">All selected skills</option><option value="any">Any selected skill</option></Select>
        {filters.skills.map(skill => <Button size="sm" key={skillKey(skill)} aria-label={`Remove ${skill} filter`} onClick={() => set('skills', filters.skills.filter(value => skillKey(value) !== skillKey(skill)))}>{skill} ×</Button>)}
      </div>}
      <div className="mt-4 flex flex-wrap gap-2">
        <Button size="sm" onClick={() => { setFilters(current => ({ ...current, designation: requirements?.title ?? designation.title, minExperience: String(requirements?.minExperience ?? designation.minExperience), skills: requirements?.skills ?? designation.requiredSkills, skillMode: 'all' })); resetSelection() }}>Use designation requirements</Button>
        <Button size="sm" variant="ghost" onClick={() => { setFilters(emptyFilters); setQuery(''); resetSelection() }}>Clear filters</Button>
      </div>
    </fieldset>
    <div className="border-b border-rule px-5 py-3 text-sm text-ink-500"><strong className="text-ink">{coverage.loading ? '…' : coverage.data?.total ?? '—'}</strong> available candidates · Already mapped and deployed candidates are excluded. An active recruiter must own the candidate.</div>
    {!orderOpen && <p role="status" className="bg-amber-50 px-5 py-3 text-sm text-amber-700">This order is {requirements?.orderStatus.replace('_', ' ')}. Open it before matching candidates.</p>}
    {error && <div role="alert" className="bg-stamp-50 p-5 text-sm text-stamp-700">{error}{refreshNeeded && <Button className="mt-3" disabled={saving} onClick={() => void refresh()}>Refresh directory</Button>}</div>}
    {loadError ? <div className="space-y-3 p-5"><p role="alert" className="text-sm text-stamp-700">{loadError}</p><Button disabled={saving} onClick={() => setRevision(value => value + 1)}>Retry matching search</Button></div>
      : !ready ? <p role="status" className="p-8 text-center text-ink-500">Searching the candidate directory…</p>
      : result.items.length === 0 ? <EmptyState title="No candidates match these filters" description="Clear or adjust the filters. Candidates need an active owner and no active application to be matched." />
      : <><ul className="divide-y divide-rule-soft">{result.items.map(candidate => <MatchRow key={candidate.id} candidate={candidate} checked={selected.includes(candidate.id)} disabled={saving || !orderOpen} onToggle={() => setPicked(current => current.includes(candidate.id) ? current.filter(id => id !== candidate.id) : [...current, candidate.id])} />)}</ul>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-rule p-5"><span className="text-sm text-ink-500">{result.page * result.pageSize + 1}–{Math.min((result.page + 1) * result.pageSize, result.total)} of {result.total}</span><div className="flex gap-2"><Button size="sm" disabled={saving || result.page === 0} onClick={() => { setPage(result.page - 1); setPicked([]) }}>Previous</Button><Button size="sm" disabled={saving || (result.page + 1) * result.pageSize >= result.total} onClick={() => { setPage(result.page + 1); setPicked([]) }}>Next</Button></div></div></>}
  </Sheet>
}

function MatchRow({ candidate, checked, disabled, onToggle }: { candidate: MatchCandidate; checked: boolean; disabled: boolean; onToggle: () => void }) {
  const store = useStore()
  const evidence = candidate.matchEvidence
  return <li className={cn('p-5', checked && 'bg-navy-50/70')}>
    <div className="flex items-start gap-3">
      <CheckboxInput className="mt-1" checked={checked} onChange={onToggle} disabled={disabled} aria-label={`Select ${candidate.name}`} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center justify-between gap-2"><button type="button" className="text-left font-semibold text-ink" disabled={disabled} onClick={onToggle}>{candidate.name}</button><Link className="inline-flex items-center gap-1 text-sm text-navy-700" to={`/candidates/${candidate.id}`} target="_blank" rel="noopener noreferrer">View profile <ExternalLink className="h-3.5 w-3.5" aria-label="Opens in a new tab" /></Link></div>
        <p className="mt-1 break-words text-sm text-ink-500">{candidate.designations.join(', ') || 'Designation not recorded'} · {candidate.totalExperience} years · {candidate.currentCity || 'Location not recorded'} · {store.user(candidate.ownerId)?.name ?? 'Assigned recruiter'}</p>
        <div className="mt-3 flex flex-wrap gap-2">{!candidate.passportNo && <Badge tone="amber">Passport number missing</Badge>}{!evidence.meetsExperience && <Badge tone="amber">Below required experience</Badge>}{candidate.status === 'not_interested' && <Badge tone="amber">Declined an earlier order</Badge>}</div>
        <div className="mt-3 space-y-2 text-sm">
          {evidence.matchedSkills.length > 0 && <div className="flex flex-wrap items-center gap-2"><span className="text-ink-500">Required skills recorded</span>{evidence.matchedSkills.map(skill => <Badge key={skillKey(skill)} tone="navy">{skill}</Badge>)}</div>}
          {evidence.missingSkills.length > 0 && <div className="flex flex-wrap items-center gap-2"><span className="text-ink-500">Not recorded</span>{evidence.missingSkills.map(skill => <Badge key={skillKey(skill)} tone="amber">{skill}</Badge>)}</div>}
          <div className="flex flex-wrap items-center gap-2"><span className="text-ink-500">Profile skills</span>{normalizedSkills(candidate.skills).length ? normalizedSkills(candidate.skills).map(skill => <Badge key={skillKey(skill)}>{skill}</Badge>) : <span className="text-amber-700">No skills recorded</span>}</div>
        </div>
      </div>
    </div>
  </li>
}
