import { ChevronDown, X } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { stageByNo } from '@/shared/workflow/stages'
import type { User } from '@/shared/model/types'
import { Field, Input, Select } from '@/shared/ui/primitives'
import { NEEDS_ASSIGNMENT_LABEL, PRD_COLUMNS, type DataBankPool } from '@/features/candidates/data-bank-pools'
import { SOURCE_LABEL } from '@/features/candidates/components/data-bank-columns'
import type { SkillCoverage } from '@/features/candidates/use-skill-facets'

const STATUS_LABEL: Record<string, string> = {
  new: 'New',
  in_process: 'In process',
  not_interested: 'Not interested',
  deployed: 'Deployed',
  needs_review: 'Needs review',
}

const POOL_LABEL: [DataBankPool, string][] = [
  ['all', 'Everyone'],
  ['unmapped', 'No order yet'],
  ['unassigned', NEEDS_ASSIGNMENT_LABEL],
  ['needs_review', 'Needs review'],
  ['not_interested', 'Not interested'],
]

const labelOf = (key: string) => PRD_COLUMNS.find((column) => column.key === key)?.label ?? key

/** The filters that live behind "More filters": text matches, exact years, order reference, date and skills. */
const HIDDEN_KEYS = ['name', 'phone', 'designation', 'experience', 'experience_min', 'experience_max', 'order', 'added', 'added_from', 'added_to']
export const hasMoreFilters = (filters: Record<string, string>, skills: string[], skillStatus: string) =>
  HIDDEN_KEYS.some((key) => (filters[key] ?? '').trim()) || skills.length > 0 || skillStatus !== 'all'

interface Props {
  filters: Record<string, string>
  onFilter: (key: string, value: string) => void
  pool: DataBankPool
  onPool: (pool: DataBankPool) => void
  staff: string
  onStaff: (staff: string) => void
  users: User[]
  skills: string[]
  onSkills: (skills: string[]) => void
  skillMode: 'all' | 'any'
  onSkillMode: (mode: 'all' | 'any') => void
  skillStatus: string
  onSkillStatus: (status: string) => void
  coverage: { data: SkillCoverage | null; loading: boolean; error: string }
  moreOpen: boolean
  onMoreToggle: () => void
  canClear: boolean
  onClear: () => void
}

/**
 * The FILTERS row of white selects (pool, status, source, passport, stage, assigned staff), a
 * "More filters" drawer for the remaining PRD columns (name, phone, designation, experience,
 * mapped order, date added) and the skills filter. Every PRD column therefore stays filterable.
 */
export function DataBankFilters(props: Props) {
  const { filters, onFilter, pool, onPool, staff, onStaff, users, skills, onSkills, skillMode, onSkillMode, skillStatus, onSkillStatus, coverage, moreOpen, onMoreToggle, canClear, onClear } = props
  const facets = coverage.data?.skills ?? []
  const moreActive = hasMoreFilters(filters, skills, skillStatus)
  const today = new Date().toISOString().slice(0, 10)
  const years = (key: 'experience_min' | 'experience_max', label: string, placeholder: string) => (
    <Field label={label} error={key === 'experience_max' && filters.experience_min && filters.experience_max && Number(filters.experience_max) < Number(filters.experience_min) ? 'Must not be less than the first number.' : undefined}>
      <Input type="number" inputMode="numeric" min={0} max={80} step={1} aria-label={label} value={filters[key] ?? ''} placeholder={placeholder} onChange={(event) => onFilter(key, event.target.value)} />
    </Field>
  )
  const day = (key: 'added_from' | 'added_to', label: string) => (
    <Field label={label} error={key === 'added_to' && filters.added_from && filters.added_to && filters.added_to < filters.added_from ? 'Must not be before the first date.' : undefined}>
      <Input type="date" aria-label={label} value={filters[key] ?? ''} max={today} onChange={(event) => onFilter(key, event.target.value)} />
    </Field>
  )
  const text = (key: string, placeholder: string, extra?: { type?: string; min?: number }) => (
    <Field label={labelOf(key)}>
      <Input aria-label={`Filter by ${labelOf(key)}`} value={filters[key] ?? ''} placeholder={placeholder} onChange={(event) => onFilter(key, event.target.value)} {...extra} />
    </Field>
  )
  const choice = (key: string, any: string, options: [string, string][]) => (
    <span className="db-sel">
      <Select aria-label={`Filter by ${labelOf(key)}`} value={filters[key] ?? ''} onChange={(event) => onFilter(key, event.target.value)}>
        <option value="">{any}</option>
        {options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </Select>
    </span>
  )
  return (
    <div className="db-filters" aria-label="Filters">
      <div className="db-filterbar">
        <span className="db-filterbar-label">Filters</span>
        <span className="db-sel">
          <Select aria-label="Show" value={pool} onChange={(event) => onPool(event.target.value as DataBankPool)}>
            {POOL_LABEL.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </Select>
        </span>
        {choice('status', 'Any status', Object.entries(STATUS_LABEL))}
        {choice('source', 'Any source', Object.values(SOURCE_LABEL).map((label) => [label, label]))}
        {choice('passport', 'Any passport', [['Yes', 'Has a passport'], ['No', 'No passport']])}
        {choice('stage', 'Any stage', [['0', 'No order yet'], ...Array.from({ length: 11 }, (_, index): [string, string] => [String(index + 1), `Stage ${index + 1} · ${stageByNo(index + 1).title}`])])}
        <span className="db-sel">
          <Select aria-label="Filter by assigned staff" value={staff} onChange={(event) => onStaff(event.target.value)}>
            <option value="">All staff</option>
            {users.filter((user) => user.role !== 'admin' || user.id === staff).map((user) => <option key={user.id} value={user.id}>{user.name}{!user.active ? ' (inactive)' : ''}</option>)}
          </Select>
        </span>
        <span className="db-more-cell">
          {canClear && <button type="button" className="db-clear" onClick={onClear}><X size={13} aria-hidden="true" />Clear</button>}
          <button type="button" className={cn('db-more-toggle', moreOpen && 'is-open')} aria-expanded={moreOpen} aria-controls="db-more-filters" onClick={onMoreToggle}>
            More filters
            {moreActive && <i className="db-more-dot" aria-label="Some of these filters are active" />}
            <ChevronDown size={15} aria-hidden="true" className="db-chevron" />
          </button>
        </span>
      </div>

      {skills.length > 0 && (
        <div className="db-chips" aria-label="Skills in use">
          {skills.map((key) => {
            const label = facets.find((facet) => facet.key === key)?.label ?? key
            return <button type="button" key={key} className="db-chip" aria-label={`Remove skill ${label}`} onClick={() => onSkills(skills.filter((skill) => skill !== key))}>{label}<X size={12} aria-hidden="true" /></button>
          })}
        </div>
      )}

      <div id="db-more-filters" className="db-more" hidden={!moreOpen}>
        <div className="db-more-grid">
          {text('name', 'Contains')}
          {text('phone', 'Contains')}
          {text('designation', 'Contains')}
          {years('experience_min', 'Experience from (years)', 'From')}
          {years('experience_max', 'Experience up to (years)', 'Up to')}
          {text('order', 'Order number')}
          {day('added_from', 'Added from')}
          {day('added_to', 'Added up to')}
        </div>
        <div className="db-skill-row">
          <span className="db-skill-title">Skills</span>
          <Select className="db-skill-select" aria-label="Filter by recorded skill" value="" disabled={coverage.loading || !!coverage.error} onChange={(event) => {
            if (event.target.value) { onSkills([...skills, event.target.value]); onSkillStatus('all') }
          }}>
            <option value="">{coverage.loading ? 'Loading skills…' : coverage.error ? 'Skills unavailable' : 'Add a skill'}</option>
            {facets.filter((facet) => !skills.includes(facet.key)).slice().sort((a, b) => b.count - a.count || a.label.localeCompare(b.label)).map((facet) => (
              <option key={facet.key} value={facet.key}>{facet.label} ({facet.count})</option>
            ))}
          </Select>
          {skills.length > 1 && (
            <Select className="db-skill-mode" aria-label="Skill matching" value={skillMode} onChange={(event) => onSkillMode(event.target.value as 'all' | 'any')}>
              <option value="all">Match all skills</option>
              <option value="any">Match any skill</option>
            </Select>
          )}
          <Select className="db-skill-mode" aria-label="Skill coverage" value={skillStatus} onChange={(event) => {
            onSkillStatus(event.target.value)
            if (event.target.value === 'missing') onSkills([])
          }}>
            <option value="all">All candidates</option>
            <option value="missing">No skills recorded{coverage.data ? ` (${coverage.data.missing})` : ''}</option>
          </Select>
        </div>
      </div>
    </div>
  )
}
