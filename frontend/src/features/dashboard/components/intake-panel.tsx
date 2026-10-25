import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { ArrowDown, ArrowRight, ArrowUp, ArrowUpRight, GitBranch, Inbox, Layers, Plus, ShieldCheck, UserPlus, type LucideIcon } from 'lucide-react'
import { SOURCE_META } from '@/features/dashboard/components/identity'
import { FlowChart, Rhythm, SourceRibbon } from '@/features/dashboard/components/flow-chart'
import { PlacementJourney } from '@/features/dashboard/components/placement-journey'
import { RecentFeed } from '@/features/dashboard/components/recent-arrivals'
import { INTAKE_SOURCES, SOURCE_KEYS, colourOf, number, percentOf, plural, type Arrivals, type IntakeOverview, type StageRow } from '@/features/dashboard/components/intake-model'
import { stageReach, trendPercent, type BucketUnit, type IntakeBucket, type Period } from '@/features/dashboard/metrics'
import '@/styles/dashboard-charts.css'

export { INTAKE_SOURCES, SOURCE_KEYS, number }
export type { Arrivals, IntakeOverview, StageRow }

const UNIT_WORD: Record<BucketUnit, string> = { day: 'day', week: 'week', month: 'month', quarter: 'quarter' }

/** A computed sentence under a chart. Every figure in it comes from the data on screen. */
function Insight({ children }: { children: ReactNode }) {
  return <p className="dc-insight"><i aria-hidden="true" /><span>{children}</span></p>
}

/** The change against the period of the same length just before it. Only drawn when there is real earlier data to compare with. */
function DeltaPill({ percent, previous }: { percent: number; previous: number }) {
  const down = percent < 0
  const flat = Math.abs(percent) < 0.05
  return <span className={`dc-pill ${flat ? 'is-flat' : down ? 'is-down' : 'is-up'}`} title={`${number(previous)} in the period of the same length before`}>
    {flat ? null : down ? <ArrowDown size={13} aria-hidden="true" /> : <ArrowUp size={13} aria-hidden="true" />}
    <b>{flat ? 'No change' : `${Math.abs(percent).toFixed(1)}%`}</b><em>vs {number(previous)} before</em>
  </span>
}

/* ------------------------------------------------------------------ stat strip */

const reducedMotion = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/** A figure that counts up from zero once when it first appears, and eases to a new value when live data changes it. */
function useCountUp(target: number) {
  const [shown, setShown] = useState(() => (reducedMotion() ? target : 0))
  const current = useRef(shown)
  useEffect(() => {
    if (reducedMotion() || current.current === target) { current.current = target; setShown(target); return }
    const from = current.current
    const length = from === 0 ? 700 : 350
    const began = performance.now()
    let frame = 0
    const tick = (now: number) => {
      const progress = Math.min(1, (now - began) / length)
      const value = Math.round(from + (target - from) * (1 - Math.pow(1 - progress, 3)))
      current.current = value
      setShown(value)
      if (progress < 1) frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [target])
  return shown
}

function Figure({ value, suffix = '' }: { value: number; suffix?: string }) {
  const shown = useCountUp(value)
  return <>{number(shown)}{suffix}</>
}

function Tile({ icon: Icon, label, value, suffix, sub, scope, tone, onClick, go }: { icon: LucideIcon; label: string; value: number; suffix?: string; sub: ReactNode; scope: string; tone: string; onClick: () => void; go: string }) {
  return <button type="button" className="dc-tile" style={{ '--tone': tone } as CSSProperties} onClick={onClick} aria-label={`${label}: ${number(value)}${suffix ?? ''}. ${scope}. ${go}`}>
    <span className="dc-tile-top"><span className="dc-ico" aria-hidden="true"><Icon size={17} strokeWidth={1.9} /></span><span className="dc-tile-label">{label}</span><ArrowUpRight className="dc-tile-go" size={16} aria-hidden="true" /></span>
    <strong className="dc-tile-value" aria-hidden="true"><Figure value={value} suffix={suffix} /></strong>
    <span className="dc-tile-sub">{sub}</span>
    <span className="dc-tile-scope" aria-hidden="true">{scope}</span>
  </button>
}

/* ------------------------------------------------------------------ where they came from */

function SourceRanks({ sources, placed, total }: { sources: Record<string, number>; placed: Record<string, number>; total: number }) {
  const rows = INTAKE_SOURCES.map(source => ({ ...source, value: sources[source.key] ?? 0, placed: placed[source.key] ?? 0 }))
    .sort((a, b) => b.value - a.value || INTAKE_SOURCES.findIndex(s => s.key === a.key) - INTAKE_SOURCES.findIndex(s => s.key === b.key))
  const max = Math.max(1, ...rows.map(row => row.value))
  return <ol className="dc-ranks" aria-label="Candidates by source">
    {rows.map(row => {
      const Icon = SOURCE_META[row.key]?.icon
      return <li key={row.key} className={row.value === 0 ? 'is-zero' : undefined} style={{ '--tone': colourOf(row.key) } as CSSProperties}>
        <span className="dc-rank-ico" aria-hidden="true">{Icon && <Icon size={15} />}</span>
        <span className="dc-rank-name">{row.label}</span>
        <span className="dc-rank-nums"><b>{number(row.value)}</b><em>{percentOf(row.value, total)}%</em></span>
        <span className="dc-rank-track" aria-hidden="true"><i style={{ width: `${row.value / max * 100}%` }} /></span>
        <span className="dc-rank-placed" title="Candidates from this source who have reached Deployed">{row.value ? <><b>{number(row.placed)}</b> placed</> : '—'}</span>
      </li>
    })}
  </ol>
}

function sourceInsight(sources: Record<string, number>, total: number): ReactNode {
  if (!total) return null
  const best = Math.max(...INTAKE_SOURCES.map(source => sources[source.key] ?? 0))
  const leaders = INTAKE_SOURCES.filter(source => (sources[source.key] ?? 0) === best)
  if (leaders.length === 1) return <><b>{leaders[0].label}</b> brought {number(best)} of {number(total)} {total === 1 ? 'candidate' : 'candidates'} ({percentOf(best, total)}%).</>
  if (leaders.length === INTAKE_SOURCES.length) return `Every source brought ${number(best)}.`
  return <><b>{leaders.map(source => source.label).join(' and ')}</b> brought {number(best)} each, out of {number(total)}.</>
}

/* ------------------------------------------------------------------ journey insights */

function stageInsights(rows: StageRow[]): ReactNode[] {
  const total = rows.reduce((sum, row) => sum + row.count, 0)
  if (!total) return []
  const counts = rows.map(row => row.count)
  const { reached, moved, holdup } = stageReach(counts)
  const best = Math.max(...counts)
  const lead = rows.filter(row => row.count === best)
  const out: ReactNode[] = [lead.length > 1
    ? <>Most candidates are waiting at <b>{lead.length} stages</b> equally ({number(best)} each, of {number(total)} active).</>
    : <>Most candidates are waiting at <b>Stage {lead[0].stage} · {lead[0].label}</b> ({number(best)} of {number(total)}).</>]
  if (holdup !== null) {
    const row = rows[holdup], share = Math.round((moved[holdup] as number) * 100)
    out.push(<>Biggest holdup: <b>Stage {row.stage} · {row.label}</b>. Only {share}% of the {number(reached[holdup])} who reached it have moved on.</>)
  }
  return out
}

/* ------------------------------------------------------------------ the whole block */

type Tone = 'info' | 'warn' | 'good' | 'bad' | 'muted' | 'accent'

export function IntakeBlock({ overview, arrivals, period, periodLabel, scope, staffId, todayCount, stages, openOrders, statusOf, onStage, onCandidate, go }: {
  overview: IntakeOverview; arrivals: Arrivals; period: Period; periodLabel: string; scope: string; staffId: string; todayCount: number; stages: StageRow[]; openOrders: number
  statusOf: (status: string) => { label: string; tone: Tone }; onStage: (stage: number) => void; onCandidate: (id: string) => void; go: (path: string) => void
}) {
  const [off, setOff] = useState<string[]>([])
  const visible = SOURCE_KEYS.filter(key => !off.includes(key))
  const toggle = (key: string) => setOff(previous => previous.includes(key) ? previous.filter(item => item !== key) : previous.length >= SOURCE_KEYS.length - 1 ? previous : [...previous, key])
  const sum = (record: Record<string, number>, keys: string[]) => keys.reduce((total, key) => total + (record[key] ?? 0), 0)
  const shownTotal = sum(overview.sources, visible)
  const shownPrevious = sum(overview.previous, visible)
  const percent = trendPercent(period, shownTotal, shownPrevious)
  const total = overview.total
  const active = stages.reduce((count, row) => count + row.count, 0)
  const byHand = sum(overview.sources, ['walk_in', 'referral', 'other'])
  const auto = sum(overview.sources, ['email', 'whatsapp'])
  const insights = stageInsights(stages)
  const rangeLabel = periodLabel.toLowerCase()
  const staffQuery = staffId ? `?staff=${encodeURIComponent(staffId)}` : ''
  const countIn = (buckets: IntakeBucket[]) => buckets.reduce((count, bucket) => count + visible.reduce((inner, key) => inner + (bucket.counts[key] ?? 0), 0), 0)

  // The card picks its form from how much there is to show: a feed for a handful, daily bars for a few dozen, wider bars beyond that.
  const mode: 'empty' | 'feed' | 'days' | 'bars' = total === 0 ? 'empty' : total < 10 ? 'feed' : total <= 60 ? 'days' : 'bars'
  const cap = overview.span ?? 30
  const stripDays = cap <= 7 ? 7 : 14
  const dailyTotal = (days: number) => (overview.daily[days] ?? []).reduce((count, bucket) => count + bucket.total, 0)
  const barDays = cap <= 7 ? 7 : dailyTotal(14) === total ? 14 : 30
  const dayBuckets = overview.daily[mode === 'days' ? barDays : stripDays] ?? []
  const unitWord = mode === 'bars' ? UNIT_WORD[overview.unit] : 'day'
  const busiestOf = (buckets: IntakeBucket[]) => buckets.reduce<{ bucket: IntakeBucket; value: number } | null>((best, bucket) => {
    const value = visible.reduce((count, key) => count + (bucket.counts[key] ?? 0), 0)
    return value > 0 && (!best || value > best.value) ? { bucket, value } : best
  }, null)
  const best = useMemo(() => busiestOf(mode === 'bars' ? overview.buckets : dayBuckets), [mode, overview.buckets, dayBuckets, visible.join(',')])
  const inStretch = countIn(dayBuckets)
  const recent = overview.recent.filter(row => visible.includes(SOURCE_KEYS.includes(row.source) ? row.source : 'other')).slice(0, 6)
  const subtitle = mode === 'feed' ? `Newest first · ${rangeLabel}` : mode === 'days' ? `Per day · last ${barDays} days` : `Per ${unitWord} · ${rangeLabel}`
  const insight = best
    ? <>Busiest {unitWord}: <b>{best.bucket.full}</b> with {plural(best.value, 'candidate')}.{mode === 'days' && inStretch < shownTotal ? ` ${number(inStretch)} of ${number(shownTotal)} arrived in these ${barDays} days.` : ''}</>
    : null

  return <div className="dc-block">
    <div className="dc-strip">
      <Tile icon={UserPlus} label="New candidates" value={overview.total} tone="var(--chart-1, var(--primary))" scope={`${scope} · ${rangeLabel}`} go="Open the Data Bank, newest first" onClick={() => go(`/data-bank${staffQuery}`)}
        sub={percent !== null ? <>{percent < 0 ? '▼' : '▲'} {Math.abs(percent).toFixed(1)}% vs {number(sum(overview.previous, SOURCE_KEYS))} before</> : todayCount > 0 ? `${number(todayCount)} added today` : 'None added today'} />
      <Tile icon={Layers} label="Email and WhatsApp" value={auto} tone="var(--chart-2, var(--brand-green))" scope={`${scope} · ${rangeLabel}`} go="Open the inbox" onClick={() => go('/inbox')}
        sub={total ? `${percentOf(auto, total)}% of all · ${number(byHand)} added by hand` : 'No candidates in this period'} />
      <Tile icon={ShieldCheck} label="Clean profiles" value={percentOf(overview.verified, total)} suffix="%" tone="var(--chart-2, var(--brand-green))" scope={`${scope} · ${rangeLabel}`} go="Open the Data Bank, profiles that need review" onClick={() => go(`/data-bank?pool=needs_review${staffQuery ? `&${staffQuery.slice(1)}` : ''}`)}
        sub={total ? `${number(overview.verified)} of ${number(total)} need no check` : 'No profiles in this period'} />
      <Tile icon={GitBranch} label="Active placements" value={active} tone="var(--chart-3, var(--warning-ink))" scope={`${scope} · right now`} go="Open the pipeline" onClick={() => go('/mapped')}
        sub={`in the 11 stages now · ${plural(openOrders, 'open order')}`} />
    </div>

    <div className="dc-intake">
      <section className={`dc-panel dc-flow is-${mode}`} aria-label="Candidates added">
        <header className="dc-head">
          <div><h3>Candidates added</h3><p>{mode === 'empty' ? rangeLabel[0].toUpperCase() + rangeLabel.slice(1) : subtitle}</p></div>
          {percent !== null && mode !== 'empty' && <DeltaPill percent={percent} previous={shownPrevious} />}
        </header>

        {mode === 'empty' && <div className="dc-empty-card">
          <span className="dc-empty-ico" aria-hidden="true"><UserPlus size={22} strokeWidth={1.7} /></span>
          <strong>No candidates were added {period === 'all' ? 'yet' : `in ${rangeLabel}`}</strong>
          <p>Add one by hand, or let the connected email accounts and WhatsApp bring them in.</p>
          <div className="dc-empty-actions">
            <button type="button" className="dc-btn is-primary" onClick={() => go('/add-candidate')}><Plus size={15} />Add candidate</button>
            <button type="button" className="dc-btn" onClick={() => go('/inbox')}><Inbox size={15} />Open inbox</button>
          </div>
        </div>}

        {mode === 'feed' && <div className="dc-adapt">
          <RecentFeed rows={recent} statusOf={statusOf} onOpen={onCandidate} />
          <div className="dc-strip14">
            <h4>Last {stripDays} days</h4>
            <FlowChart buckets={dayBuckets} unit="day" visible={visible} unitWord="day" compact />
          </div>
        </div>}
        {mode === 'days' && <FlowChart buckets={dayBuckets} unit="day" visible={visible} unitWord="day" />}
        {mode === 'bars' && <FlowChart buckets={overview.buckets} unit={overview.unit} visible={visible} unitWord={unitWord} />}

        {mode !== 'empty' && <>
          <SourceRibbon overview={overview} visible={visible} toggle={toggle} />
          <div className="dc-flow-insight">
            {insight && <Insight>{insight}</Insight>}
            <button type="button" className="dc-link" onClick={() => go(`/data-bank${staffQuery}`)}>Open Data Bank<ArrowRight size={13} /></button>
          </div>
        </>}
      </section>

      <section className="dc-panel dc-source-card" aria-label="Where they came from">
        <header className="dc-head"><div><h3>Where they came from</h3><p>Ranked by candidates added · {rangeLabel}</p></div></header>
        <SourceRanks sources={overview.sources} placed={overview.placed} total={total} />
        {total > 0 && <footer className="dc-foot"><Insight>{sourceInsight(overview.sources, total)}</Insight></footer>}
      </section>
    </div>

    <section className="dc-panel dc-rhythm-card" aria-label="Arrival rhythm">
      <header className="dc-head"><div><h3>Arrival rhythm</h3><p>Which days candidates arrive on, over the last 12 weeks. Darker means more.</p></div></header>
      <Rhythm arrivals={arrivals} />
    </section>

    <section className="dc-panel dc-stages" aria-label="Placement journey">
      <header className="dc-head">
        <div><h3>Placement journey</h3><p>Where active candidates are waiting on the way to deployment. Select a stage to see who is in it.</p></div>
        <div className="dc-head-end"><span className="dc-chip"><b>{number(active)}</b> active</span><button type="button" className="dc-link" onClick={() => go('/mapped')}>Open pipeline<ArrowRight size={13} /></button></div>
      </header>
      {active === 0 && <p className="dc-note" role="status">Nobody is in the stages right now. Candidates appear here as soon as they are matched to an order.</p>}
      <PlacementJourney rows={stages} onOpen={onStage} />
      {insights.length > 0 && <footer className="dc-foot is-row">{insights.map((line, index) => <Insight key={index}>{line}</Insight>)}</footer>}
    </section>
  </div>
}

export function IntakeSkeleton() {
  return <div className="dc-block dc-skeleton" role="status" aria-label="Loading your dashboard">
    <div className="dc-strip">{[0, 1, 2, 3].map(index => <div key={index} className="dc-tile dc-skel"><i /><i /><i /></div>)}</div>
    <div className="dc-intake"><div className="dc-panel dc-skel is-tall"><i /><i className="is-plot" /></div><div className="dc-panel dc-skel"><i /><i className="is-plot" /></div></div>
  </div>
}
