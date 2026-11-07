import { useMemo } from 'react'
import { Avatar } from '@/features/clients/components/parts'
import { jobsLabel, type ClientOption } from '@/features/jobs/job-form'
import { SearchSelect, type SelectItem } from '@/features/jobs/components/search-select'
import '@/styles/jobs-simple.css'

/** One company of one client as a dropdown entry: the company in bold, the contact person muted, and how many jobs it already has. */
export function clientItem(option: ClientOption): SelectItem {
  return { key: option.companyId, label: option.company, sub: option.contact, note: jobsLabel(option.jobs), avatar: true, search: [option.company, option.contact, option.country, option.city] }
}

/**
 * The clients that already exist, as a searchable dropdown (see SearchSelect), one line per company. Fixed from the client's own page:
 * shown, not chosen.
 */
export function ClientPicker({ options, value, onChange, locked, disabled, invalid, emptyText }: {
  options: ClientOption[]
  value: string
  onChange: (option: ClientOption) => void
  locked?: boolean
  disabled?: boolean
  invalid?: boolean
  emptyText: string
}) {
  const items = useMemo(() => options.map(clientItem), [options])
  const selected = options.find((option) => option.companyId === value)
  if (locked && selected) {
    return <div className="js-picker">
      <div className="js-fixed">
        <Avatar name={selected.company} size="sm" />
        <span className="js-opt-text"><b>{selected.company}</b><small>{selected.contact}</small></span>
        <span className="js-opt-jobs">{jobsLabel(selected.jobs)}</span>
      </div>
    </div>
  }
  return <SearchSelect items={items} value={value} placeholder="Search and choose a client" ariaLabel="Client" searchPlaceholder="Type a company or contact name"
    emptyText={emptyText} disabled={disabled} invalid={invalid} onChange={(item) => { const option = options.find((entry) => entry.companyId === item.key); if (option) onChange(option) }} />
}
