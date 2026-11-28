import type { ReactNode } from 'react'
import { cn } from '@/shared/lib/cn'
import { Select } from '@/shared/ui/primitives'
import { DatePicker } from '@/shared/ui/date-picker'
import { PRESETS, type RangeChoice } from './daily-report-view'

interface Props {
  choice: RangeChoice
  onChoice: (choice: RangeChoice) => void
  /** The dates the report is showing now: a Custom range starts from them. */
  range: { from: string; to: string }
  today: string
  /** The recruiter filter: the people listed (empty or `team` false hides it). */
  people: { id: string; name: string }[]
  staff: string
  onPickStaff: (id: string) => void
  team: boolean
  live: ReactNode
}

/**
 * The one toolbar row of the Reports page (Task report and Lead sheet report): the period (Today / Yesterday / This week / This month / Total / Custom range)
 * on the left, the recruiter filter and the Live label on the right.
 * Built like the Job requirements toolbar: a bordered panel, a segmented control, labelled filters.
 */
export function ReportToolbar(props: Props) {
  const { choice, onChoice, range, today } = props
  const custom = choice.preset === 'range'
  return <section className="rp-toolbar" aria-label="Report period and filters">
    <div className="rp-toolbar-row">
      <div className="rp-toolbar-main">
        <div className="rp-seg" role="group" aria-label="Report period">
          {PRESETS.map(item => <button key={item.key} type="button" aria-pressed={choice.preset === item.key} className={cn(choice.preset === item.key && 'is-on')}
            onClick={() => onChoice({ preset: item.key, from: '', to: '' })}>{item.label}</button>)}
          <button type="button" aria-pressed={custom} className={cn(custom && 'is-on')} onClick={() => onChoice({ preset: 'range', from: range.from, to: range.to })}>Custom range</button>
        </div>
        {custom && <div className="rp-dates" role="group" aria-label="Custom range">
          <DatePicker compact aria-label="From" value={range.from} max={range.to} onChange={value => value && onChoice({ preset: 'range', from: value, to: range.to })} />
          <span aria-hidden="true">to</span>
          <DatePicker compact aria-label="To" value={range.to} min={range.from} max={today} onChange={value => value && onChoice({ preset: 'range', from: range.from, to: value })} />
        </div>}
      </div>
      <div className="rp-toolbar-end">
        {props.team && props.people.length > 0 && <label className="rp-filter"><span>Recruiter</span>
          <Select compact aria-label="Filter by recruiter" value={props.staff || 'all'} onChange={event => props.onPickStaff(event.target.value === 'all' ? '' : event.target.value)}>
            <option value="all">All recruiters</option>
            {props.people.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
          </Select>
        </label>}
        {props.live}
      </div>
    </div>
  </section>
}
