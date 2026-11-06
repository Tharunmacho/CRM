import { useMemo, useState } from 'react'
import { ArrowRight } from 'lucide-react'
import { INTAKE_SOURCES, SOURCE_KEYS, colourOf, number, plural } from '@/features/dashboard/components/intake-model'
import { PERIODS, inWindow, periodStart, type Period } from '@/features/dashboard/metrics'

interface Arrival { addedAt: string; source: string; status?: string; needsReview?: boolean }
interface Slice { key: string; label: string; color: string; value: number }

/** Referral and anything else the screen does not name is counted under "Other", so the parts always add up. */
const sourceKey = (source: string) => (SOURCE_KEYS.includes(source) ? source : 'other')
const percent = (part: number, total: number) => (total ? Math.round(part / total * 100) : 0)

/** Where a candidate stands. The colours are the dashboard's own hues; every slice is also named in text. */
const STATUSES = [
  { key: 'new', label: 'New', color: 'var(--ds-blue)' },
  { key: 'in_process', label: 'In process', color: 'var(--ds-violet)' },
  { key: 'needs_review', label: 'Needs review', color: 'var(--ds-amber)' },
  { key: 'deployed', label: 'Deployed', color: 'var(--ds-green)' },
  { key: 'not_interested', label: 'Not interested', color: 'var(--ds-slate)' },
] as const
const statusKey = (candidate: Arrival) => {
  const status = candidate.needsReview ? 'needs_review' : candidate.status ?? 'new'
  return STATUSES.some(item => item.key === status) ? status : 'new'
}

const SIZE = 232, STROKE = 30, RADIUS = (SIZE - STROKE) / 2 - 4, CIRCUMFERENCE = 2 * Math.PI * RADIUS

/** A round chart of the shares. Each arc is a slice; the centre shows the total, or the slice under the pointer. */
function Donut({ title, slices, visible, hover, onHover }: { title: string; slices: Slice[]; visible: string[]; hover: string | null; onHover: (key: string | null) => void }) {
  const parts = slices.filter(slice => visible.includes(slice.key) && slice.value > 0)
  const total = parts.reduce((sum, slice) => sum + slice.value, 0)
  const gap = parts.length > 1 ? 4 : 0
  let offset = 0
  const active = hover ? slices.find(slice => slice.key === hover && visible.includes(slice.key)) : undefined
  return <div className="sg-donut">
    <svg viewBox={`0 0 ${SIZE} ${SIZE}`} role="img" aria-label={`${title}: ${slices.map(slice => `${slice.label} ${slice.value}`).join(', ')}.`} onMouseLeave={() => onHover(null)}>
      <circle cx={SIZE / 2} cy={SIZE / 2} r={RADIUS} className="sg-ring" strokeWidth={STROKE} />
      <g transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}>
        {parts.map(slice => {
          const length = slice.value / total * CIRCUMFERENCE
          const start = offset
          offset += length
          const drawn = Math.max(0, length - gap)
          return <circle key={slice.key} cx={SIZE / 2} cy={SIZE / 2} r={RADIUS} fill="none" stroke={slice.color} strokeWidth={hover === slice.key ? STROKE + 6 : STROKE}
            strokeDasharray={`${drawn} ${CIRCUMFERENCE - drawn}`} strokeDashoffset={-start} className={`sg-arc${hover && hover !== slice.key ? ' is-dim' : ''}`}
            onMouseEnter={() => onHover(slice.key)} onClick={() => onHover(hover === slice.key ? null : slice.key)} />
        })}
      </g>
    </svg>
    <div className="sg-centre" aria-hidden="true">
      <b>{number(active ? active.value : total)}</b>
      <span>{active ? `${active.label} · ${percent(active.value, total)}%` : total === 1 ? 'candidate' : 'candidates'}</span>
    </div>
  </div>
}

/** One round graph with its legend: a count and a share per slice. The names show or hide a slice. */
function RoundCard({ title, subtitle, slices, onOpen }: { title: string; subtitle: string; slices: Slice[]; onOpen: () => void }) {
  const [off, setOff] = useState<string[]>([])
  const [hover, setHover] = useState<string | null>(null)
  const visible = slices.map(slice => slice.key).filter(key => !off.includes(key))
  const toggle = (key: string) => setOff(previous => previous.includes(key) ? previous.filter(item => item !== key) : previous.length >= slices.length - 1 ? previous : [...previous, key])
  const all = slices.reduce((sum, slice) => sum + slice.value, 0)
  const shown = slices.filter(slice => visible.includes(slice.key)).reduce((sum, slice) => sum + slice.value, 0)
  const top = slices.reduce((best, slice) => (slice.value > best.value ? slice : best), slices[0])
  return <section className="sg-card" aria-label={title}>
    <header className="sg-head"><div><h2>{title}</h2><p>{subtitle}</p></div></header>
    <div className="sg-body">
      <Donut title={title} slices={slices} visible={visible} hover={hover} onHover={setHover} />
      <div className="sg-side">
        <ul className="sg-list" aria-label={`Show or hide a slice of ${title}`}>{slices.map(slice => {
          const on = visible.includes(slice.key)
          return <li key={slice.key}>
            <button type="button" aria-pressed={on} className={`sg-row${on ? '' : ' is-off'}${slice.value === 0 ? ' is-zero' : ''}${hover === slice.key ? ' is-hot' : ''}`} title={on ? `Hide ${slice.label}` : `Show ${slice.label}`}
              onClick={() => toggle(slice.key)} onMouseEnter={() => on && setHover(slice.key)} onMouseLeave={() => setHover(null)}>
              <i style={{ background: slice.color }} aria-hidden="true" />
              <span className="sg-name">{slice.label}</span>
              <b>{number(slice.value)}</b>
              <em>{percent(slice.value, all)}%</em>
              <span className="sg-bar" aria-hidden="true"><s style={{ width: `${percent(slice.value, all)}%`, background: slice.color }} /></span>
            </button>
          </li>
        })}</ul>
        <p className="sg-insight">{all === 0 ? 'No candidates in this period yet.' : <><b>{top.label}</b> is the largest: {plural(top.value, 'candidate')} ({percent(top.value, all)}%).{shown < all ? ` Showing ${number(shown)} of ${number(all)}.` : ''}</>}</p>
        <button type="button" className="sg-link" onClick={onOpen}>Open Data Bank<ArrowRight size={13} /></button>
      </div>
    </div>
  </section>
}

/**
 * The candidates as round graphs: where they came from (Email, WhatsApp, Direct, Other) and where they stand (New, In process,
 * Needs review, Deployed, Not interested), over one chosen period. Everything is computed from the candidates already loaded,
 * so the graphs follow the workspace live.
 */
export function CandidateGraphs({ candidates, anchor, onOpen }: { candidates: Arrival[]; anchor?: string; onOpen: () => void }) {
  const [period, setPeriod] = useState<Period>('year')
  const { sources, statuses, all } = useMemo(() => {
    const end = Date.now()
    const pool = inWindow(candidates, periodStart(period, new Date(end), candidates), end)
    return {
      all: pool.length,
      sources: INTAKE_SOURCES.map(source => ({ key: source.key, label: source.label, color: colourOf(source.key), value: pool.filter(candidate => sourceKey(candidate.source) === source.key).length })),
      statuses: STATUSES.map(status => ({ ...status, value: pool.filter(candidate => statusKey(candidate) === status.key).length })),
    }
    // The anchor moves whenever the page reloads, so the window always ends at "now".
  }, [candidates, period, anchor])
  const range = (PERIODS.find(option => option.value === period)?.label ?? '').toLowerCase()
  const subtitle = `${plural(all, 'candidate')} · ${range}`
  return <div className="sg-section">
    <div className="sg-bar-top">
      <h2>Candidates</h2>
      <div className="sg-seg" role="group" aria-label="Period">{PERIODS.map(option => <button key={option.value} type="button" aria-pressed={period === option.value} className={period === option.value ? 'is-active' : ''} onClick={() => setPeriod(option.value)}>{option.short}</button>)}</div>
    </div>
    <div className="sg-grid">
      <RoundCard title="Where they came from" subtitle={subtitle} slices={sources} onOpen={onOpen} />
      <RoundCard title="Where they stand" subtitle={subtitle} slices={statuses} onOpen={onOpen} />
    </div>
  </div>
}
