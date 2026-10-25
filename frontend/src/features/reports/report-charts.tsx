import { lazy, Suspense, useEffect, useState, type ComponentProps, type ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'

export function useNarrow(query = '(max-width: 640px)') {
  const [narrow, setNarrow] = useState(() => typeof window !== 'undefined' && window.matchMedia(query).matches)
  useEffect(() => { const list = window.matchMedia(query); const on = () => setNarrow(list.matches); list.addEventListener('change', on); return () => list.removeEventListener('change', on) }, [query])
  return narrow
}

export interface Series { key: string; name: string; colour: string }

export function Legend({ items }: { items: Pick<Series, 'name' | 'colour'>[] }) {
  if (items.length < 2) return null
  return <ul className="rv-legend" aria-label="Chart legend">{items.map(item => <li key={item.name}><i style={{ background: item.colour }} />{item.name}</li>)}</ul>
}

/** A flat chart card: title and reading hint, optional actions, legend, the plot, one computed insight and an accessible summary. */
export function ChartCard({ title, hint, legend, summary, empty, emptyHint, insight, actions, children, className = '' }: {
  title: string; hint?: string; legend?: Series[]; summary: string; empty?: boolean; emptyHint?: string; insight?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string
}) {
  return <figure className={`rv-card rv-chart ${className}`} aria-label={`${title}. ${summary}`}>
    <figcaption><span className="rv-cap"><strong>{title}</strong>{hint && <span>{hint}</span>}</span>{actions}</figcaption>
    {legend && <Legend items={legend} />}
    {empty
      ? <div className="rv-chart-empty" role="status"><strong>Nothing to show yet</strong><p>{emptyHint ?? 'No data for this period. Choose a longer date range or clear a filter.'}</p></div>
      : <>{children}{insight && <p className="rv-insight"><i aria-hidden="true" /><span>{insight}</span></p>}</>}
  </figure>
}

/* The step-area chart is drawn by recharts, which lives in its own chunk and is fetched only when a report shows it. */
const StepAreaImpl = lazy(() => import('./report-recharts').then(m => ({ default: m.StepArea })))

const plotFallback = (height: number) => <div className="rv-plot-loading" role="status" aria-label="Loading chart" style={{ height }} />

export function StepArea(props: ComponentProps<typeof StepAreaImpl>) {
  return <Suspense fallback={plotFallback(props.height ?? 260)}><StepAreaImpl {...props} /></Suspense>
}

/* ---------------------------------------------------------------- donut */

export interface Slice { key: string; label: ReactNode; text: string; value: number; colour: string }

/** A ring and the list that names it: the arc and the legend percentage come from the same division. */
export function Donut({ slices, centre, centreLabel }: { slices: Slice[]; centre: string; centreLabel: string }) {
  const size = 168, stroke = 24, radius = (size - stroke) / 2, circumference = 2 * Math.PI * radius
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
