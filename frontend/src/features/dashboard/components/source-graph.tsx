import { useMemo, useState, type CSSProperties } from 'react'
import { ArrowRight, PieChart, UserCheck } from 'lucide-react'
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

const SIZE = 240, STROKE = 26, RADIUS = (SIZE - STROKE) / 2 - 6, CIRCUMFERENCE = 2 * Math.PI * RADIUS

/** A round chart of the shares. Each arc is a slice; the centre shows the total, or the slice under the pointer. */
function Donut({ title, unit, slices, visible, hover, onHover }: { title: string; unit: [string, string]; slices: Slice[]; visible: string[]; hover: string | null; onHover: (key: string | null) => void }) {
  const parts = slices.filter(slice => visible.includes(slice.key) && slice.value > 0)
  const total = parts.reduce((sum, slice) => sum + slice.value, 0)
  const many = parts.length > 1
  // Rounded ends reach half a stroke beyond each arc, so the gap between arcs is a stroke wide plus a little air.
  const gap = many ? STROKE + 5 : 0
  let offset = 0
  const active = hover ? slices.find(slice => slice.key === hover && visible.includes(slice.key)) : undefined
  return <div className="sg-donut">
    <svg viewBox={`0 0 ${SIZE} ${SIZE}`} role="img" aria-label={`${title}: ${slices.map(slice => `${slice.label} ${slice.value}`).join(', ')}.`} onMouseLeave={() => onHover(null)}>
      <circle cx={SIZE / 2} cy={SIZE / 2} r={RADIUS} className="sg-ring" strokeWidth={STROKE} />
      <circle cx={SIZE / 2} cy={SIZE / 2} r={RADIUS - STROKE / 2 - 8} className="sg-disc" />
      <g transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}>
        {parts.map(slice => {
          const length = slice.value / total * CIRCUMFERENCE
          const start = offset
          offset += length
          // A tiny share still shows as a dot; the arc starts half a gap in so the round caps do not touch.
          const drawn = many ? Math.max(0.01, length - gap) : CIRCUMFERENCE
          return <circle key={`${slice.key}:${Math.round(length)}`} cx={SIZE / 2} cy={SIZE / 2} r={RADIUS} fill="none" stroke={slice.color} strokeWidth={hover === slice.key ? STROKE + 5 : STROKE}
            strokeLinecap={many ? 'round' : 'butt'} strokeDasharray={`${drawn} ${CIRCUMFERENCE - drawn}`} strokeDashoffset={-(start + (many ? gap / 2 : 0))}
            className={`sg-arc${hover === slice.key ? ' is-hot' : ''}${hover && hover !== slice.key ? ' is-dim' : ''}`} style={{ '--c': CIRCUMFERENCE, '--d': drawn, '--glow': slice.color } as CSSProperties}
            onMouseEnter={() => onHover(slice.key)} onClick={() => onHover(hover === slice.key ? null : slice.key)} />
        })}
      </g>
    </svg>
    <div className="sg-centre" aria-hidden="true">
      <b>{number(active ? active.value : total)}</b>
      <span>{active ? `${active.label} · ${percent(active.value, total)}%` : total === 1 ? unit[0] : unit[1]}</span>
    </div>
  </div>
}

/** One round graph with its legend: a count and a share per slice. The names show or hide a slice. */
function RoundCard({ title, subtitle, icon: Icon, tone, unit, slices, empty, openLabel, onOpen }: {
  title: string; subtitle: string; icon: typeof PieChart; tone: string; unit: [string, string]; slices: Slice[]; empty: string; openLabel: string; onOpen: () => void
}) {
  const [off, setOff] = useState<string[]>([])
  const [hover, setHover] = useState<string | null>(null)
  const visible = slices.map(slice => slice.key).filter(key => !off.includes(key))
  const toggle = (key: string) => setOff(previous => previous.includes(key) ? previous.filter(item => item !== key) : previous.length >= slices.length - 1 ? previous : [...previous, key])
  const all = slices.reduce((sum, slice) => sum + slice.value, 0)
  const shown = slices.filter(slice => visible.includes(slice.key)).reduce((sum, slice) => sum + slice.value, 0)
  const top = slices.reduce((best, slice) => (slice.value > best.value ? slice : best), slices[0])
  return <section className={`sg-card ${tone}`} aria-label={title}>
    <header className="sg-head">
      <span className="sg-chip"><Icon size={18} strokeWidth={1.9} /></span>
      <div><h2>{title}</h2><p>{subtitle}</p></div>
    </header>
    <Donut title={title} unit={unit} slices={slices} visible={visible} hover={hover} onHover={setHover} />
    <ul className="sg-list" aria-label={`Show or hide a slice of ${title}`}>{slices.map(slice => {
      const on = visible.includes(slice.key)
      return <li key={slice.key}>
        <button type="button" aria-pressed={on} style={{ '--slice': slice.color } as CSSProperties} className={`sg-row${on ? '' : ' is-off'}${slice.value === 0 ? ' is-zero' : ''}${hover === slice.key ? ' is-hot' : ''}`} title={on ? `Hide ${slice.label}` : `Show ${slice.label}`}
          onClick={() => toggle(slice.key)} onMouseEnter={() => on && setHover(slice.key)} onMouseLeave={() => setHover(null)}>
          <i aria-hidden="true" />
          <span className="sg-name">{slice.label}</span>
          <b>{number(slice.value)}</b>
          <em>{percent(slice.value, all)}%</em>
          <span className="sg-bar" aria-hidden="true"><s style={{ width: `${percent(slice.value, all)}%` }} /></span>
        </button>
      </li>
    })}</ul>
    <p className="sg-insight">{all === 0 ? empty : <><b>{top.label}</b> is the largest: {number(top.value)} ({percent(top.value, all)}%).{shown < all ? ` Showing ${number(shown)} of ${number(all)}.` : ''}</>}</p>
    <button type="button" className="sg-open" onClick={onOpen}>{openLabel}<ArrowRight size={15} /></button>
  </section>
}

/**
 * The dashboard's round graphs, over one chosen period: where candidates came from (Email, WhatsApp, Direct, Other) and where
 * they stand (New, In process, Needs review, Deployed, Not interested). Everything is computed from the candidates already
 * loaded, so the graphs follow the workspace live.
 */
export function DashboardGraphs({ candidates, anchor, onOpen }: { candidates: Arrival[]; anchor?: string; onOpen: () => void }) {
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
      <h2>Overview</h2>
      <div className="sg-seg" role="group" aria-label="Period">{PERIODS.map(option => <button key={option.value} type="button" aria-pressed={period === option.value} className={period === option.value ? 'is-active' : ''} onClick={() => setPeriod(option.value)}>{option.short}</button>)}</div>
    </div>
    <div className="sg-grid">
      <RoundCard title="Where they came from" subtitle={subtitle} icon={PieChart} tone="sg-blue" unit={['candidate', 'candidates']} slices={sources} empty="No candidates in this period yet." openLabel="Open Data Bank" onOpen={onOpen} />
      <RoundCard title="Where they stand" subtitle={subtitle} icon={UserCheck} tone="sg-violet" unit={['candidate', 'candidates']} slices={statuses} empty="No candidates in this period yet." openLabel="Open Data Bank" onOpen={onOpen} />
    </div>
  </div>
}
