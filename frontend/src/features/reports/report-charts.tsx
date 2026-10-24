import { useEffect, useState, type ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { Pipeline } from './report-config'

export function useNarrow(query = '(max-width: 640px)') {
  const [narrow, setNarrow] = useState(() => typeof window !== 'undefined' && window.matchMedia(query).matches)
  useEffect(() => { const list = window.matchMedia(query); const on = () => setNarrow(list.matches); list.addEventListener('change', on); return () => list.removeEventListener('change', on) }, [query])
  return narrow
}

/** Chart ink comes from the theme tokens, so it follows palette and dark mode. */
const AXIS = { fontSize: 12, fill: 'var(--text-muted)', fontFamily: 'Poppins, system-ui, sans-serif' }
const AXIS_TITLE = { ...AXIS, fontSize: 11.5, fill: 'var(--text-light)' }
const GRID = 'var(--border-main)'
const CURSOR = { fill: 'color-mix(in srgb, var(--primary) 7%, transparent)' }

export interface Series { key: string; name: string; colour: string }

export function Legend({ items }: { items: Pick<Series, 'name' | 'colour'>[] }) {
  if (items.length < 2) return null
  return <ul className="rv-legend" aria-label="Chart legend">{items.map(item => <li key={item.name}><i style={{ background: item.colour }} />{item.name}</li>)}</ul>
}

/** A flat chart card: title and reading hint, legend, the plot and an accessible summary. */
export function ChartCard({ title, hint, legend, summary, empty, emptyHint, children, className = '' }: {
  title: string; hint?: string; legend?: Series[]; summary: string; empty?: boolean; emptyHint?: string; children: ReactNode; className?: string
}) {
  return <figure className={`rv-card rv-chart ${className}`} aria-label={`${title}. ${summary}`}>
    <figcaption><strong>{title}</strong>{hint && <span>{hint}</span>}</figcaption>
    {legend && <Legend items={legend} />}
    {empty
      ? <div className="rv-chart-empty" role="status"><strong>Nothing to show yet</strong><p>{emptyHint ?? 'No data for this period. Choose a longer date range or clear a filter.'}</p></div>
      : children}
  </figure>
}

function TooltipBox({ active, payload, label, unit, labelFormat }: { active?: boolean; payload?: { name?: string; value?: number | string; color?: string; fill?: string }[]; label?: string | number; unit?: string; labelFormat?: (value: string | number) => string }) {
  if (!active || !payload?.length) return null
  return <div className="rv-tooltip">
    <p>{labelFormat && label != null ? labelFormat(label) : label}</p>
    {payload.map(item => <div key={item.name}><i style={{ background: item.color || item.fill }} /><span>{item.name}</span><b className="tnum">{typeof item.value === 'number' ? item.value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : item.value}{unit ? ` ${unit}` : ''}</b></div>)}
  </div>
}

/** Horizontal bars: one row per category, optionally stacked. Best for long category names. */
export function HBars({ data, category, series, stacked, valueLabel, unit, height, labelWidth = 150 }: {
  data: object[]; category: string; series: Series[]; stacked?: boolean; valueLabel: string; unit?: string; height?: number; labelWidth?: number
}) {
  const size = height ?? Math.max(180, data.length * (stacked || series.length === 1 ? 34 : 24 * series.length + 14) + 56)
  const narrow = useNarrow()
  const width = narrow ? Math.min(labelWidth, 112) : labelWidth
  const clip = (value: string) => { const text = String(value); const room = Math.floor(width / 6.6); return text.length > room ? `${text.slice(0, room - 1)}…` : text }
  return <div style={{ height: size }} className="rv-plot">
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 18, bottom: 22, left: 4 }} barCategoryGap={stacked ? 8 : 10} barGap={2}>
        <CartesianGrid horizontal={false} stroke={GRID} strokeOpacity={0.7} />
        <XAxis type="number" allowDecimals={false} tick={AXIS} axisLine={false} tickLine={false} label={{ value: valueLabel, position: 'insideBottom', offset: -14, style: AXIS_TITLE }} />
        <YAxis type="category" dataKey={category} width={width} tick={AXIS} tickFormatter={clip} axisLine={{ stroke: GRID }} tickLine={false} interval={0} />
        <Tooltip cursor={CURSOR} content={<TooltipBox unit={unit} />} />
        {series.map(item => <Bar key={item.key} dataKey={item.key} name={item.name} fill={item.colour} stackId={stacked ? 'a' : undefined} stroke={stacked ? 'var(--bg-card)' : undefined} strokeWidth={stacked ? 1.5 : 0} radius={stacked ? 0 : [0, 4, 4, 0]} maxBarSize={22} isAnimationActive={false} />)}
      </BarChart>
    </ResponsiveContainer>
  </div>
}

/** Vertical columns: a few categories on the x axis, optionally stacked. */
export function Columns({ data, category, series, stacked, valueLabel, categoryLabel, unit, height = 280, tickFormat, labelFormat, money, barMax = 36 }: {
  data: object[]; category: string; series: Series[]; stacked?: boolean; valueLabel: string; categoryLabel?: string; unit?: string; height?: number
  tickFormat?: (value: string) => string; labelFormat?: (value: string | number) => string; money?: boolean; barMax?: number
}) {
  return <div style={{ height }} className="rv-plot">
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 8, right: 14, bottom: categoryLabel ? 26 : 8, left: 6 }} barCategoryGap="22%" barGap={3}>
        <CartesianGrid vertical={false} stroke={GRID} strokeOpacity={0.7} />
        <XAxis dataKey={category} tick={AXIS} tickFormatter={tickFormat} axisLine={{ stroke: GRID }} tickLine={false} interval={0} label={categoryLabel ? { value: categoryLabel, position: 'insideBottom', offset: -16, style: AXIS_TITLE } : undefined} />
        <YAxis allowDecimals={false} tick={AXIS} axisLine={false} tickLine={false} width={money ? 76 : 52} tickFormatter={money ? (v: number) => v.toLocaleString() : undefined} label={{ value: valueLabel, angle: -90, position: 'insideLeft', offset: 0, style: { ...AXIS_TITLE, textAnchor: 'middle' } }} />
        <Tooltip cursor={CURSOR} content={<TooltipBox unit={unit} labelFormat={labelFormat} />} />
        {series.map(item => <Bar key={item.key} dataKey={item.key} name={item.name} fill={item.colour} stackId={stacked ? 'a' : undefined} stroke={stacked ? 'var(--bg-card)' : undefined} strokeWidth={stacked ? 1.5 : 0} radius={stacked ? 0 : [4, 4, 0, 0]} maxBarSize={barMax} isAnimationActive={false} />)}
      </BarChart>
    </ResponsiveContainer>
  </div>
}

/* ---------------------------------------------------------------- donut */

export interface Slice { key: string; label: ReactNode; text: string; value: number; colour: string }

/** A ring and the list that names it: the arc and the legend percentage come from the same division. */
export function Donut({ slices, centre, centreLabel }: { slices: Slice[]; centre: string; centreLabel: string }) {
  const size = 156, stroke = 34, radius = (size - stroke) / 2, circumference = 2 * Math.PI * radius
  const total = slices.reduce((sum, slice) => sum + slice.value, 0)
  let used = 0
  return <div className="rv-donut">
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={slices.map(s => `${s.text} ${s.value}`).join(', ')}>
      <circle cx={size / 2} cy={size / 2} r={radius} fill="none" strokeWidth={stroke} stroke="var(--tint-2)" />
      {total > 0 && slices.map(slice => {
        if (!slice.value) return null
        const share = slice.value / total, length = share * circumference, offset = used * circumference
        used += share
        return <circle key={slice.key} cx={size / 2} cy={size / 2} r={radius} fill="none" stroke={slice.colour} strokeWidth={stroke}
          strokeDasharray={`${Math.max(0, length - (slices.filter(s => s.value).length > 1 ? 1.5 : 0))} ${circumference}`} strokeDashoffset={-offset} transform={`rotate(-90 ${size / 2} ${size / 2})`}>
          <title>{`${slice.text}: ${slice.value.toLocaleString()} (${Math.round(share * 100)}%)`}</title>
        </circle>
      })}
      <text x={size / 2} y={size / 2 - 4} textAnchor="middle" dominantBaseline="central" className="rv-donut-centre">{centre}</text>
      <text x={size / 2} y={size / 2 + 16} textAnchor="middle" dominantBaseline="central" className="rv-donut-sub">{centreLabel}</text>
    </svg>
    <ul className="rv-donut-list">
      {slices.map(slice => <li key={slice.key}>
        <span className="rv-donut-name">{slice.label}</span>
        <b className="tnum">{slice.value.toLocaleString()}</b>
        <span className="rv-donut-pct tnum">{total ? Math.round((slice.value / total) * 100) : 0}%</span>
      </li>)}
    </ul>
  </div>
}

/* --------------------------------------------------------------- funnel */

function niceMax(value: number) {
  // A multiple of four so the quarter gridlines land on whole numbers.
  if (value <= 200) return Math.max(4, Math.ceil(value / 4) * 4)
  const step = Math.pow(10, Math.floor(Math.log10(value / 4)))
  return Math.ceil(value / 4 / step) * step * 4
}

/** The 11-stage funnel: one segmented bar per stage and the drop-off to the next stage beside it. */
export function Funnel({ rows, dropOff }: { rows: Pipeline[]; dropOff: (index: number) => { lost: number; percent: number } | null }) {
  const top = niceMax(Math.max(1, ...rows.map(r => r.reached)))
  const pct = (n: number) => `${(n / top) * 100}%`
  const ticks = [0, 0.25, 0.5, 0.75, 1]
  return <div className="rv-funnel" role="list" aria-label="Applications per stage">
    <div className="rv-fn-head" aria-hidden="true"><span>Stage</span><span>Applications that reached the stage</span><span className="is-end">Reached</span><span className="is-end">Lost to next stage</span></div>
    {rows.map((row, index) => {
      const drop = dropOff(index)
      return <div className="rv-fn-row" role="listitem" key={row.stage}
        title={`Stage ${row.stage}, ${row.label}: ${row.reached} reached (${row.active} active or waiting, ${row.progressed} moved on, ${row.closed} ended here)`}>
        <span className="rv-fn-name"><b className="tnum">{row.stage}</b><span>{row.label}</span></span>
        <span className="rv-fn-track">
          {ticks.slice(1, 4).map(t => <i className="rv-fn-grid" key={t} style={{ left: `${t * 100}%` }} />)}
          <span className="rv-fn-bar" style={{ width: pct(row.reached) }}>
            {row.active > 0 && <i className="is-active" style={{ flexGrow: row.active }} />}
            {row.progressed > 0 && <i className="is-progressed" style={{ flexGrow: row.progressed }} />}
            {row.closed > 0 && <i className="is-closed" style={{ flexGrow: row.closed }} />}
          </span>
        </span>
        <span className="rv-fn-count tnum">{row.reached.toLocaleString()}</span>
        <span className="rv-fn-drop">{drop ? (drop.lost > 0 ? <em className="tnum">−{drop.lost.toLocaleString()} <small>{drop.percent}%</small></em> : <em className="is-none tnum">0</em>) : <small>Final stage</small>}</span>
      </div>
    })}
    <div className="rv-fn-axis" aria-hidden="true"><span />
      <span className="rv-fn-ticks">{ticks.map(t => <i key={t} style={{ left: `${t * 100}%` }}>{Math.round(top * t).toLocaleString()}</i>)}</span>
      <span /><span />
    </div>
  </div>
}

/* ----------------------------------------------------------- mini stats */

export interface Mini { label: string; value: ReactNode; detail?: string; colour?: string; icon?: LucideIcon }

/** Small stat cards inside a report; each carries the colour its series uses in the charts below. */
export function MiniStats({ items }: { items: Mini[] }) {
  return <div className="rv-minis">{items.map(item => <section key={item.label} className="rv-card rv-mini" aria-label={item.label} style={item.colour ? { ['--tone' as string]: item.colour } : undefined}>
    <p>{item.icon && <span className="rv-mini-icon"><item.icon size={15} strokeWidth={1.9} /></span>}<span>{item.label}</span></p>
    <strong className="tnum">{item.value}</strong>
    {item.detail && <small>{item.detail}</small>}
  </section>)}</div>
}
