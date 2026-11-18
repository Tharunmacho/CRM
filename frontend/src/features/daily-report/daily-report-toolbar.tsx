import type { ReactNode } from 'react'
import { Select } from '@/shared/ui/primitives'
import { PRESETS, type RangeChoice } from './daily-report-view'

interface Props {
  choice: RangeChoice
  onChoice: (choice: RangeChoice) => void
  client: string
  clients: { id: string; name: string }[]
  onClient: (id: string) => void
  live: ReactNode
}

/**
 * The period row under the tabs: Today / Yesterday / This week / This month / Total on the left, the client and the Live label on
 * the right. A range that came from an older link is shown as it is, with no button pressed.
 */
export function PeriodRow(props: Props) {
  const { choice, onChoice } = props
  return <section className="dwr-period" aria-label="Report period and filters">
    <div className="dwr-period-main">
      <div className="dwr-seg" role="group" aria-label="Report period">
        {PRESETS.map(item => <button key={item.key} type="button" aria-pressed={choice.preset === item.key} onClick={() => onChoice({ preset: item.key, from: '', to: '' })}>{item.label}</button>)}
      </div>
    </div>
    <div className="dwr-period-end">
      {props.clients.length > 0 && <Select aria-label="Client" className="tk-select" value={props.client} onChange={event => props.onClient(event.target.value)}>
        <option value="all">All clients</option>
        {props.clients.map(option => <option key={option.id} value={option.id}>{option.name}</option>)}
      </Select>}
      {props.live}
    </div>
  </section>
}
