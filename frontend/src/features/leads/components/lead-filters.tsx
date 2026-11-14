import { ChevronDown, X } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import type { User } from '@/shared/model/types'
import { Field, Input, Select } from '@/shared/ui/primitives'
import { DATE_FIELDS, DATE_PRESETS, LEAD_STATUSES, NEW_CARD_DETAIL, PLATFORM_LABEL, activePreset, platformKind, presetRange, todayKey, type LeadFacets, type LeadFilters } from '../lead-model'

interface Props {
  filters: LeadFilters
  facets: LeadFacets | null
  users: User[]
  meId: string
  onChange: (patch: Partial<LeadFilters>) => void
  moreOpen: boolean
  onMoreToggle: () => void
  canClear: boolean
  onClear: () => void
  /** Only an admin sees every lead, so only an admin gets the "Unassigned" and per-recruiter choices; managers and staff see only their own leads. */
  canAssign: boolean
}

/** Keep a value the data no longer offers visible, so a saved filter never silently disappears. */
const withCurrent = (list: string[] | undefined, current: string) => (current && !(list ?? []).includes(current) ? [current, ...(list ?? [])] : list ?? [])

/**
 * The filter row of white selects (campaign, position, country, status, platform, assigned to) and a "More filters"
 * drawer for the date range. Same markup and styles as the Data Bank's filters, so the two screens read alike.
 */
export function LeadFiltersBar({ filters, facets, users, meId, onChange, moreOpen, onMoreToggle, canClear, onClear, canAssign }: Props) {
  const today = todayKey()
  const choice = (key: keyof LeadFilters, label: string, any: string, options: [string, string][]) => (
    <span className="db-sel">
      <Select aria-label={label} value={filters[key] as string} onChange={(event) => onChange({ [key]: event.target.value } as Partial<LeadFilters>)}>
        <option value="">{any}</option>
        {options.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
      </Select>
    </span>
  )
  const plain = (list: string[] | undefined, current: string): [string, string][] => withCurrent(list, current).map((item) => [item, item])
  const dateActive = Boolean(filters.from || filters.to || filters.recent)
  const dateLabel = DATE_FIELDS.find((field) => field.value === filters.dateField)?.label ?? 'Uploaded'
  const preset = activePreset(filters)
  return (
    <div className="db-filters ld-filters" aria-label="Filters">
      <div className="db-filterbar" style={{ '--db-cols': 6 } as React.CSSProperties}>
        <span className="db-filterbar-label">Filters</span>
        {choice('campaign', 'Filter by campaign', 'Any campaign', plain(facets?.campaigns, filters.campaign))}
        {choice('position', 'Filter by job', 'Any job', plain(facets?.positions, filters.position))}
        {choice('country', 'Filter by country', 'Any country', plain(facets?.countries, filters.country))}
        {choice('status', 'Filter by status', 'Any status', LEAD_STATUSES.map((status) => [status.value, status.label]))}
        {choice('platform', 'Filter by platform', 'Any platform', withCurrent(facets?.platforms, filters.platform).map((item) => [item, `${PLATFORM_LABEL[platformKind(item)]}${platformKind(item) === 'other' ? ` (${item})` : ''}`]))}
        <span className="db-sel">
          <Select aria-label="Filter by who the lead is assigned to" value={filters.assigned} onChange={(event) => onChange({ assigned: event.target.value })}>
            <option value="">All leads</option>
            <option value="me">Assigned to me</option>
            {canAssign && <option value="none">Unassigned</option>}
            {canAssign && users.filter((user) => user.role !== 'admin' && user.id !== meId).map((user) => <option key={user.id} value={user.id}>{user.name}{!user.active ? ' (inactive)' : ''}</option>)}
          </Select>
        </span>
        <span className="db-more-cell">
          {canClear && <button type="button" className="db-clear" onClick={onClear}><X size={13} aria-hidden="true" />Clear</button>}
          <button type="button" className={cn('db-more-toggle', moreOpen && 'is-open')} aria-expanded={moreOpen} aria-controls="ld-more-filters" onClick={onMoreToggle}>
            Dates
            {dateActive && <i className="db-more-dot" aria-label="A date filter is active" />}
            <ChevronDown size={15} aria-hidden="true" className="db-chevron" />
          </button>
        </span>
      </div>
      {(filters.batchId || filters.recent) && (
        <div className="db-chips" aria-label="Filters in use">
          {filters.batchId && <button type="button" className="db-chip" aria-label="Show every upload again" onClick={() => onChange({ batchId: '' })}>From one upload<X size={12} aria-hidden="true" /></button>}
          {filters.recent && <button type="button" className="db-chip" aria-label="Stop showing only the last 24 hours of uploads" onClick={() => onChange({ recent: '' })}>{NEW_CARD_DETAIL}<X size={12} aria-hidden="true" /></button>}
        </div>
      )}
      <div id="ld-more-filters" className="db-more" hidden={!moreOpen}>
        <div className="ld-datebar">
          <div className="db-seg ld-seg" role="group" aria-label="Which date to filter on">
            {DATE_FIELDS.map((field) => (
              <button key={field.value} type="button" className={cn('db-seg-btn', filters.dateField === field.value && 'is-on')} aria-pressed={filters.dateField === field.value}
                title={field.value === 'uploaded' ? 'When the lead was imported into the CRM' : "The lead's own date from the ad platform"} onClick={() => onChange({ dateField: field.value })}>{field.label}</button>
            ))}
          </div>
          <div className="db-seg ld-seg" role="group" aria-label="Quick dates">
            {DATE_PRESETS.map(({ id, label }) => (
              <button key={id} type="button" className={cn('db-seg-btn', preset === id && 'is-on')} aria-pressed={preset === id}
                onClick={() => onChange(preset === id ? { from: '', to: '' } : { ...presetRange(id), recent: '' })}>{label}</button>
            ))}
          </div>
        </div>
        <div className="db-more-grid ld-dates">
          <Field label={`${dateLabel} from`} error={filters.from && filters.to && filters.to < filters.from ? 'Must not be before the first date.' : undefined}>
            <Input type="date" aria-label={`${dateLabel} from`} value={filters.from} max={today} onChange={(event) => onChange({ from: event.target.value, recent: '' })} />
          </Field>
          <Field label={`${dateLabel} up to`}>
            <Input type="date" aria-label={`${dateLabel} up to`} value={filters.to} max={today} onChange={(event) => onChange({ to: event.target.value, recent: '' })} />
          </Field>
        </div>
      </div>
    </div>
  )
}
