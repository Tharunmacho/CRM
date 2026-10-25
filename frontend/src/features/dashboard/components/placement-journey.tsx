import { useMemo, type CSSProperties } from 'react'
import { Avatar } from '@/features/dashboard/components/identity'
import { number, plural, type StageRow } from '@/features/dashboard/components/intake-model'
import { stageReach } from '@/features/dashboard/metrics'

const SHOWN = 3
const waiting = (days: number | null) => (days === null ? '' : days === 0 ? 'since today' : `waiting ${plural(days, 'day')}`)

/**
 * The eleven stages as one journey: a track that is filled up to the furthest stage anyone has reached, a node per stage,
 * and the candidates who are waiting there stacked above it. The busiest stage is lit; a stage nobody is in stays, dimmed.
 */
export function PlacementJourney({ rows, onOpen }: { rows: StageRow[]; onOpen: (stage: number) => void }) {
  const counts = rows.map(row => row.count)
  const { reached, moved, holdup } = useMemo(() => stageReach(counts), [counts.join(',')])
  const total = counts.reduce((sum, value) => sum + value, 0)
  const furthest = counts.reduce((last, value, index) => (value > 0 ? index : last), -1)
  const busiest = total ? counts.indexOf(Math.max(...counts)) : -1
  const stackRows = Math.max(0, ...rows.map(row => Math.min(SHOWN, row.people.length) + (row.count > SHOWN ? 0.75 : 0)))
  const last = rows.length - 1
  return <ol className="dc-j" aria-label="Placement journey: active candidates by stage" style={{ '--stack': stackRows } as CSSProperties}>
    {rows.map((row, index) => {
      const shown = row.people.slice(0, SHOWN)
      const more = row.count - shown.length
      const share = moved[index]
      const side = index < 2 ? 'is-l' : index > last - 2 ? 'is-r' : ''
      return <li key={row.stage} style={{ '--p': last ? index / last : 0 } as CSSProperties}
        className={`${index <= furthest ? 'is-done' : ''}${index > 0 && index <= furthest ? ' has-in' : ''}${index < furthest ? ' has-out' : ''}${row.count === 0 ? ' is-zero' : ''}${busiest === index ? ' is-busiest' : ''}${holdup === index ? ' is-hold' : ''}`}>
        <button type="button" className="dc-j-stage" onClick={() => onOpen(row.stage)}
          aria-label={`Stage ${row.stage}: ${row.label}. ${row.count === 0 ? 'Nobody is waiting here.' : `${plural(row.count, 'candidate')} waiting${shown.length ? `: ${shown.map(person => person.name).join(', ')}${more > 0 ? ` and ${more} more` : ''}` : ''}.`}${busiest === index ? ' The busiest stage.' : ''}${holdup === index && share !== null ? ` Biggest holdup: ${Math.round(share * 100)} percent of those who reached it have moved on.` : ''} Show who is in it.`}>
          <span className="dc-j-people" aria-hidden="true">
            {more > 0 && <i className="dc-j-more">+{number(more)}</i>}
            {[...shown].reverse().map(person => <span key={person.id} className="dc-j-av" title={person.name}><Avatar name={person.name} size="sm" /></span>)}
          </span>
          <span className="dc-j-rail" aria-hidden="true"><i className="dc-j-node">{row.stage}</i></span>
          <b className="dc-j-count" aria-hidden="true">{number(row.count)}</b>
          <span className="dc-j-name" aria-hidden="true">{row.label}</span>
          {holdup === index && share !== null && <em className="dc-j-hold" aria-hidden="true">{Math.round(share * 100)}% moved on</em>}
          <span className={`dc-j-tip ${side}`} role="presentation" aria-hidden="true">
            <b>Stage {row.stage} · {row.label}</b>
            {row.count === 0 ? <small>Nobody is waiting here.</small> : <>
              <ul>{row.people.slice(0, 6).map(person => <li key={person.id}><span>{person.name}</span><small>{waiting(person.days)}</small></li>)}</ul>
              {row.count > 6 && <small>and {number(row.count - 6)} more</small>}
            </>}
            <small className="dc-j-cta">Select to see who is in this stage</small>
          </span>
          {/* Vertical layout (phones): the people sit beside the name instead of above the node. */}
          <span className="dc-j-people-inline" aria-hidden="true">
            {shown.map(person => <span key={person.id} className="dc-j-av" title={person.name}><Avatar name={person.name} size="sm" /></span>)}
            {more > 0 && <i className="dc-j-more">+{number(more)}</i>}
          </span>
          <span className="sr-only">{reached[index]} reached this stage or went further.</span>
        </button>
      </li>
    })}
  </ol>
}
