import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { INTAKE_SOURCES, colourOf, number, plural, type Arrivals, type IntakeOverview } from '@/features/dashboard/components/intake-model'
import { axisFor, type ArrivalDay, type BucketUnit, type IntakeBucket } from '@/features/dashboard/metrics'


/** Measures an element so the SVG can be drawn at its real size (crisp text, no stretched circles). */
function useSize<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  useEffect(() => {
    const element = ref.current
    if (!element) return
    setSize({ width: element.clientWidth, height: element.clientHeight })
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(entries => setSize({ width: Math.round(entries[0].contentRect.width), height: Math.round(entries[0].contentRect.height) }))
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  return [ref, size] as const
}

export function FlowChart({ buckets, unit, visible, unitWord, compact = false }: { buckets: IntakeBucket[]; unit: BucketUnit; visible: string[]; unitWord: string; compact?: boolean }) {
  const shown = (bucket: IntakeBucket) => visible.reduce((sum, key) => sum + (bucket.counts[key] ?? 0), 0)
  const sparse = buckets.reduce((sum, bucket) => sum + shown(bucket), 0) < 10
  const [ref, { width, height }] = useSize<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const id = useId().replace(/:/g, '')
  const n = buckets.length
  const values = buckets.map(shown)
  const peak = Math.max(0, ...values)
  const { max, ticks } = axisFor(peak)
  const pad = compact ? { l: 4, r: 4, t: 24, b: 24 } : { l: 34, r: 10, t: 26, b: 28 }
  const W = Math.max(0, width), H = Math.max(0, height)
  const innerW = Math.max(1, W - pad.l - pad.r), innerH = Math.max(1, H - pad.t - pad.b)
  const slot = innerW / Math.max(1, n)
  const barW = Math.max(3, Math.min(sparse && !compact ? 76 : compact ? 28 : 46, slot * (n > 40 ? 0.7 : 0.6)))
  const radius = Math.min(7, barW / 2)
  const base = pad.t + innerH
  const cx = (i: number) => pad.l + (i + 0.5) * slot
  const y = (v: number) => base - v / max * innerH
  const labelWidth = unit === 'day' || unit === 'week' ? 50 : 42
  const stride = Math.max(1, Math.ceil((labelWidth + 6) / slot))
  const labelAll = slot >= 22 || n <= 14
  const active = hover !== null ? buckets[hover] : null
  const summary = `Candidates added per ${unitWord}. ${buckets.map(bucket => `${bucket.full}: ${shown(bucket)}`).join(', ')}.`
  const index = (clientX: number, box: DOMRect) => Math.max(0, Math.min(n - 1, Math.floor((clientX - box.left - pad.l) / slot)))
  const empty = peak === 0
  const tipLeft = active ? Math.min(Math.max(cx(hover!), 110), Math.max(110, W - 110)) : 0
  const replay = `${visible.join(',')}:${n}`
  const latestWord = unit === 'day' ? 'Today' : `This ${unitWord}`
  return <div className={`dc-flow-chart${sparse ? ' is-sparse' : ''}${compact ? ' is-compact' : ''}`} ref={ref} onMouseLeave={() => setHover(null)}>
    {W > 0 && H > 0 && n > 0 && <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={summary}
      onMouseMove={event => setHover(index(event.clientX, event.currentTarget.getBoundingClientRect()))}
      onClick={event => { const next = index(event.clientX, event.currentTarget.getBoundingClientRect()); setHover(hover === next ? null : next) }}>
      {!compact && ticks.slice(1).map(tick => <line key={tick} x1={pad.l} x2={W - pad.r} y1={y(tick)} y2={y(tick)} className="dc-fc-grid" />)}
      {!compact && ticks.map(tick => <text key={`t-${tick}`} x={pad.l - 8} y={y(tick) + 4} textAnchor="end" className="dc-fc-axis">{number(tick)}</text>)}
      {slot > 14 && <rect x={pad.l + (n - 1) * slot + 2} y={pad.t - 6} width={Math.max(0, slot - 4)} height={innerH + 6} rx="8" className="dc-fc-latest" />}
      {hover !== null && slot > 8 && <rect x={pad.l + hover * slot + 1} y={pad.t - 6} width={Math.max(0, slot - 2)} height={innerH + 6} rx="8" className="dc-fc-hot" />}
      <g key={replay}>
        {buckets.map((bucket, i) => {
          const total = values[i]
          const x0 = cx(i) - barW / 2
          if (total === 0) return <rect key={bucket.from} x={cx(i) - Math.min(barW, 14) / 2} y={base - 3} width={Math.min(barW, 14)} height={3} rx="1.5" className="dc-fc-tick" />
          const h = total / max * innerH
          let cursor = base
          const parts = INTAKE_SOURCES.filter(source => visible.includes(source.key) && bucket.counts[source.key] > 0)
          return <g key={bucket.from} className="dc-fc-bar" style={{ animationDelay: `${Math.min(i, 30) * 14}ms` }} clipPath={`url(#clip-${id}-${i})`}>
            <clipPath id={`clip-${id}-${i}`}><rect x={x0} y={base - h} width={barW} height={h + radius} rx={radius} /></clipPath>
            {parts.map(source => {
              const part = bucket.counts[source.key] / total * h
              cursor -= part
              return <rect key={source.key} x={x0} y={cursor} width={barW} height={part + 0.5} fill={colourOf(source.key)} className="dc-fc-seg" />
            })}
          </g>
        })}
      </g>
      <line x1={pad.l} x2={W - pad.r} y1={base} y2={base} className="dc-fc-zero" />
      {buckets.map((bucket, i) => values[i] > 0 && (labelAll || values[i] === peak || i === n - 1) && <text key={`v-${bucket.from}`} x={cx(i)} y={y(values[i]) - 7} textAnchor="middle" className="dc-fc-val">{number(values[i])}</text>)}
      {buckets.map((bucket, i) => (n - 1 - i) % stride === 0 && <text key={`x-${bucket.from}`} x={Math.min(Math.max(cx(i), pad.l + 12), W - pad.r - 12)} y={H - 8} textAnchor="middle" className={`dc-fc-x${i === n - 1 ? ' is-now' : ''}`}>{i === n - 1 && stride === 1 && slot >= 44 ? latestWord : bucket.label}</text>)}
    </svg>}
    {empty && <p className="dc-fc-empty" role="status"><strong>No candidates in this stretch</strong></p>}
    {active && <div className="dc-tip" style={{ left: tipLeft, top: Math.max(2, y(values[hover!]) - 126) }} aria-hidden="true">
      <p>{active.full}</p>
      <b>{number(shown(active))} <span>{shown(active) === 1 ? 'candidate' : 'candidates'}</span></b>
      {INTAKE_SOURCES.filter(source => visible.includes(source.key) && active.counts[source.key] > 0).map(source =>
        <div key={source.key}><i style={{ background: colourOf(source.key) }} /><span>{source.label}</span><em>{number(active.counts[source.key])}</em></div>)}
    </div>}
    <ul className="sr-only" aria-label={`Candidates added per ${unitWord}, by source`}>{buckets.map(bucket => <li key={bucket.from}>{bucket.full}: {shown(bucket)} {shown(bucket) === 1 ? 'candidate' : 'candidates'}{INTAKE_SOURCES.filter(source => visible.includes(source.key) && bucket.counts[source.key] > 0).map(source => `, ${source.label} ${bucket.counts[source.key]}`).join('')}</li>)}</ul>
  </div>
}

/** A slim ribbon of the period's sources; the names under it are the filter. */
export function SourceRibbon({ overview, visible, toggle }: { overview: IntakeOverview; visible: string[]; toggle: (key: string) => void }) {
  const total = INTAKE_SOURCES.reduce((sum, source) => sum + (overview.sources[source.key] ?? 0), 0)
  return <div className="dc-ribbon-block">
    <div className="dc-ribbon" role="img" aria-label={`Sources: ${INTAKE_SOURCES.map(source => `${source.label} ${overview.sources[source.key] ?? 0}`).join(', ')}`}>
      {total > 0 ? INTAKE_SOURCES.filter(source => (overview.sources[source.key] ?? 0) > 0).map(source =>
        <i key={source.key} className={visible.includes(source.key) ? '' : 'is-off'} style={{ flexGrow: overview.sources[source.key], background: colourOf(source.key) }} title={`${source.label}: ${number(overview.sources[source.key])}`} />) : <i className="is-empty" />}
    </div>
    <div className="dc-lg" role="group" aria-label="Show or hide a source">
      {INTAKE_SOURCES.map(source => {
        const on = visible.includes(source.key)
        const value = overview.sources[source.key] ?? 0
        return <button key={source.key} type="button" aria-pressed={on} className={`${on ? 'is-on' : ''}${value === 0 ? ' is-zero' : ''}`} onClick={() => toggle(source.key)} title={on ? `Hide ${source.label}` : `Show ${source.label}`}>
          <i style={{ background: colourOf(source.key) }} aria-hidden="true" />{source.label}<b>{number(value)}</b>
        </button>
      })}
    </div>
  </div>
}

/* ------------------------------------------------------------------ arrivals rhythm */

const level = (count: number, max: number) => (count <= 0 ? 0 : Math.max(1, Math.ceil(count / Math.max(1, max) * 4)))

export function Rhythm({ arrivals }: { arrivals: Arrivals }) {
  const { columns, max, total, activeDays, elapsed, busiest } = arrivals
  const monthOf = (column: ArrivalDay[]) => new Date(column[0].time).toLocaleDateString('en', { month: 'short' })
  const perActive = activeDays ? total / activeDays : 0
  const summary = total ? `${total} candidates arrived over the last 12 weeks, on ${activeDays} of ${elapsed} days.${busiest ? ` Busiest day ${busiest.label}, ${busiest.count}.` : ''}` : 'No candidates arrived in the last 12 weeks.'
  const stat = (label: string, value: ReactNode, sub?: ReactNode) => <div><dt>{label}</dt><dd>{value}</dd>{sub && <small>{sub}</small>}</div>
  return <div className="dc-rhythm">
    <div className="dc-heat">
      <div className="dc-heat-wrap" role="img" aria-label={summary}>
        <div className="dc-heat-months" aria-hidden="true">{columns.map((column, i) => <span key={column[0].time}>{i === 0 || monthOf(column) !== monthOf(columns[i - 1]) ? monthOf(column) : ''}</span>)}</div>
        <div className="dc-heat-body">
          <div className="dc-heat-days" aria-hidden="true"><span>Mon</span><span /><span>Wed</span><span /><span>Fri</span><span /><span /></div>
          <div className="dc-heat-grid" aria-hidden="true">
            {columns.map((column, i) => column.map(day => <i key={day.time} className={`dc-cell lv-${level(day.count, max)}${day.future ? ' is-future' : ''}${i < 2 ? ' is-l' : i > columns.length - 3 ? ' is-r' : ''}`}
              data-tip={day.future ? undefined : `${day.label}: ${day.count === 0 ? 'none' : plural(day.count, 'candidate')}`} />))}
          </div>
        </div>
      </div>
      <div className="dc-heat-key" aria-hidden="true">Fewer<i className="dc-cell lv-0" /><i className="dc-cell lv-1" /><i className="dc-cell lv-2" /><i className="dc-cell lv-3" /><i className="dc-cell lv-4" />More</div>
    </div>
    <div className="dc-rhythm-copy">
      <dl>
        {stat('Days with arrivals', total ? <>{activeDays}<small> of {elapsed}</small></> : '0')}
        {stat('Busiest day', busiest ? busiest.label : 'None yet', busiest ? plural(busiest.count, 'candidate') : undefined)}
        {stat('When they arrive', activeDays ? `${perActive >= 10 ? Math.round(perActive) : perActive.toFixed(1)}` : '—', activeDays ? 'per day with arrivals' : undefined)}
      </dl>
    </div>
  </div>
}
