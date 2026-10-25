import { useState, type ReactNode } from 'react'
import { Ban, BarChart3, Banknote, Footprints, HeartPulse, Mail, MessageCircle, Table2, UserMinus, UserX, Users, Workflow } from 'lucide-react'
import { DataTable, type Column } from '@/shared/ui/table'
import { EmptyState, StatusPill } from '@/shared/ui/display'
import { RecordIdentity } from '@/shared/ui/workspace'
import { useStore } from '@/shared/state/crm-store'
import type { ReportResult } from './report-api'
import { ChartCard, Donut, MiniStats, StepArea, type Series, type Slice } from './report-charts'
import { Lollipops, MailboxShare, MonthBars, OrderLadders, ReasonBars, Segmented, StepFunnel, type RankRow, type ReasonRow } from './report-formats'
import { FillBar, Flag, SOURCE_META, SourceTag, StageChip } from './report-parts'
import { MANUAL_SOURCES, REASON_COLOURS, SERIES, SOURCE_COLOURS, fmtDate, fmtHours, fmtMonth, fmtMoney, pct, stageDropOff } from './report-config'

type Data = ReportResult
type Go = (path: string) => void
type Stack = 768 | 1024 | 1280

const num = (value: number) => value.toLocaleString()
const plural = (count: number, one: string, many = `${one}s`) => `${num(count)} ${count === 1 ? one : many}`

function RTable<T>({ rows, columns, rowKey, onRowClick, title, caption, pageSize = 12, stackBelow = 1024, className = '' }: {
  rows: T[]; columns: (c: Builders<T>) => Column<T>[]; rowKey: (row: T) => string; onRowClick?: (row: T) => void; title: string; caption?: string; pageSize?: number; stackBelow?: Stack; className?: string
}) {
  return <section className={`rv-card rv-table ${className}`} aria-label={title}>
    <header><h3>{title}</h3>{caption && <p>{caption}</p>}</header>
    <DataTable compact stackBelow={stackBelow} rows={rows} columns={columns(builders<T>())} rowKey={rowKey} pageSize={pageSize} onRowClick={onRowClick}
      empty={<EmptyState title="Nothing to show for this period" description="Choose a longer date range or clear a filter." icon={<BarChart3 size={26} strokeWidth={1.4} />} />} />
  </section>
}

interface Builders<T> {
  col: (key: string, header: string, width: string, value: (row: T) => string | number | null | undefined, extra?: Partial<Column<T>>) => Column<T>
  num: (key: string, header: string, width: string, value: (row: T) => number | null | undefined, render?: (row: T) => ReactNode) => Column<T>
}
function builders<T>(): Builders<T> {
  return {
    col: (key, header, width, value, extra = {}) => ({ key, header, width, sortable: true, value, render: row => { const v = value(row); return v == null || v === '' ? '—' : typeof v === 'number' ? <span className="tnum">{v.toLocaleString()}</span> : <span className="rv-cell" title={v}>{v}</span> }, ...extra }),
    num: (key, header, width, value, render) => ({ key, header, width, sortable: true, align: 'right', value, render: render ?? (row => { const v = value(row); return v == null ? '—' : <span className="tnum">{v.toLocaleString()}</span> }) }),
  }
}

/* ------------------------------------------------------------------ pipeline */

export function FunnelReport({ data }: { data: Data }) {
  const rows = data.pipeline
  const [view, setView] = useState<'chart' | 'table'>('chart')
  const series: Series[] = [{ key: 'active', name: 'Active or waiting', colour: SERIES.primary }, { key: 'progressed', name: 'Moved on', colour: SERIES.soft }, { key: 'closed', name: 'Ended here', colour: SERIES.bad }]
  const empty = rows.every(r => r.reached === 0)
  const losses = rows.map((row, index) => ({ row, drop: stageDropOff(rows, index) })).filter((item): item is { row: typeof rows[number]; drop: { lost: number; percent: number } } => !!item.drop && item.drop.lost > 0)
  const worst = losses.reduce<typeof losses[number] | null>((best, item) => (!best || item.drop.lost > best.drop.lost ? item : best), null)
  const next = worst ? rows[rows.indexOf(worst.row) + 1] : null
  const table = <RTable rows={rows} rowKey={r => String(r.stage)} title="Stage detail" caption="Lost before next stage counts applications that reached a stage but never reached the next one."
    columns={c => [
      c.col('stage', 'Stage', '24%', r => `${r.stage}. ${r.label}`, { render: r => <StageChip stage={r.stage} label={r.label} /> }),
      c.num('reached', 'Reached', '11%', r => r.reached), c.num('active', 'Active', '12%', r => r.active),
      c.num('progressed', 'Moved on', '13%', r => r.progressed), c.num('closed', 'Ended here', '13%', r => r.closed),
      c.num('closure', '% ended here', '12%', r => r.dropOffPercent, r => <span className="tnum">{r.dropOffPercent}%</span>),
      c.num('drop', 'Lost before next', '15%', r => stageDropOff(rows, rows.indexOf(r))?.lost, r => { const d = stageDropOff(rows, rows.indexOf(r)); return d ? <span className="tnum">{d.lost.toLocaleString()} <em className="rv-sub">({d.percent}%)</em></span> : <span className="rv-sub">Last stage</span> }),
    ]} />
  return <>
    <ChartCard title="Pipeline funnel" hint="Each step is as wide as the applications that reached it. The pill between two steps is how many were lost before the next stage." legend={view === 'chart' ? series : undefined}
      summary={`${rows[0]?.reached ?? 0} applications reached stage 1 and ${rows[rows.length - 1]?.reached ?? 0} reached stage ${rows.length}.`} empty={empty}
      emptyHint="No candidate was matched to an order in this period. Match candidates to orders, or choose a longer date range."
      actions={!empty && <Segmented label="Show the pipeline as" value={view} onChange={setView} options={[{ key: 'chart', label: <><Workflow size={13} aria-hidden="true" />Funnel</> }, { key: 'table', label: <><Table2 size={13} aria-hidden="true" />Table</> }]} />}
      insight={view === 'chart' ? worst && next ? <>Biggest loss: between <b>Stage {worst.row.stage} · {worst.row.label}</b> and <b>Stage {next.stage}</b>, {plural(worst.drop.lost, 'application')} ({worst.drop.percent}% of those who reached it).</> : 'No application was lost between stages in this period.' : undefined}>
      {view === 'chart' ? <StepFunnel rows={rows} dropOff={index => stageDropOff(rows, index)} /> : null}
    </ChartCard>
    {view === 'table' && table}
  </>
}

/* ------------------------------------------------------------------ staff */

const METRICS = [
  { key: 'assigned', label: 'Assigned', long: 'candidates assigned', colour: SERIES.primary },
  { key: 'completedTasks', label: 'Tasks finished', long: 'tasks finished', colour: SERIES.teal },
  { key: 'escalations', label: 'Sent to admin', long: 'tasks sent to the admin', colour: SERIES.amber },
  { key: 'missedUpdates', label: 'Missed updates', long: 'missed daily updates', colour: SERIES.rose },
] as const
type MetricKey = typeof METRICS[number]['key']

export function StaffReport({ data }: { data: Data }) {
  const [metric, setMetric] = useState<MetricKey>('assigned')
  const active = METRICS.find(item => item.key === metric)!
  const noStaff = !data.staff.length
  const noHours = data.stageHours.every(s => s.averageHours == null)
  const ranked = [...data.staff].sort((a, b) => b[metric] - a[metric] || a.name.localeCompare(b.name))
  const board: RankRow[] = ranked.map(s => ({ key: s.id, name: s.name, person: true, value: s[metric], label: num(s[metric]), sub: `${num(s.completedTasks)} done · ${fmtHours(s.averageStageHours)} per stage` }))
  const top = ranked[0]
  const leaders = top ? ranked.filter(s => s[metric] === top[metric]) : []
  const hours: RankRow[] = data.stageHours.map(s => ({ key: String(s.stage), stage: s.stage, name: s.label, value: s.averageHours, label: fmtHours(s.averageHours), sub: s.completed ? `${plural(s.completed, 'task')} finished` : 'No finished task' }))
  const slowest = data.stageHours.filter(s => s.averageHours != null).reduce<typeof data.stageHours[number] | null>((best, s) => (!best || (s.averageHours as number) > (best.averageHours as number) ? s : best), null)
  return <>
    <div className="rv-stack">
      <ChartCard title="Staff board" hint={`Ranked by ${active.long}, highest first. Pick another measure to re-rank.`} empty={noStaff} emptyHint="No staff match these filters." summary={`${data.staff.length} staff members ranked by ${active.long}.`}
        actions={!noStaff && <Segmented label="Rank staff by" value={metric} onChange={setMetric} options={METRICS.map(item => ({ key: item.key, label: item.label }))} />}
        insight={top && top[metric] > 0 ? (leaders.length === 1 ? <><b>{top.name}</b> leads with {num(top[metric])} {active.long}.</> : <><b>{plural(leaders.length, 'person', 'people')}</b> tie at {num(top[metric])} {active.long}.</>) : `No ${active.long} in this period.`}>
        <Lollipops rows={board} colour={active.colour} label={`Staff ranked by ${active.long}`} />
      </ChartCard>
      <ChartCard title="Average hours per stage" hint="Time from when a task is created to when its stage is finished, across the staff shown." empty={noHours}
        emptyHint="No stage was finished in this period, so there is no average time to show." summary="Average completion time for each of the 11 stages."
        insight={slowest ? <>Slowest stage: <b>Stage {slowest.stage} · {slowest.label}</b>, {fmtHours(slowest.averageHours)} on average.</> : undefined}>
        <Lollipops rows={hours} colour={SERIES.violet} ranked={false} label="Average hours for each stage" />
      </ChartCard>
    </div>
    <RTable rows={data.staff} rowKey={r => r.id} title="Staff detail" caption="One row per person. Admins and managers see the whole team. Staff see only themselves."
      columns={c => [
        c.col('name', 'Staff', '25%', r => r.name, { render: r => <RecordIdentity name={r.name} /> }), c.num('assigned', 'Assigned', '12%', r => r.assigned), c.num('done', 'Tasks done', '12%', r => r.completedTasks),
        c.num('hours', 'Avg hours / stage', '14%', r => r.averageStageHours, r => <span className="tnum">{fmtHours(r.averageStageHours)}</span>),
        c.num('samples', 'Tasks timed', '11%', r => r.durationSamples), c.num('esc', 'Sent to admin', '13%', r => r.escalations), c.num('missed', 'Missed updates', '13%', r => r.missedUpdates),
      ]} />
    <RTable rows={data.stageHours} rowKey={r => String(r.stage)} title="Average hours by stage" caption="Stages with no finished task have no average. They are not counted as zero." pageSize={11}
      columns={c => [
        c.col('stage', 'Stage', '50%', r => `${r.stage}. ${r.label}`, { render: r => <StageChip stage={r.stage} label={r.label} /> }), c.num('samples', 'Tasks finished', '20%', r => r.completed),
        c.num('hours', 'Average hours', '30%', r => r.averageHours, r => <span className="tnum">{fmtHours(r.averageHours)}</span>),
      ]} />
  </>
}

/* ------------------------------------------------------------------ sources */

export function SourceReport({ data }: { data: Data }) {
  const count = (key: string) => data.sources.find(s => s.source === key)?.count ?? 0
  const manual = data.sources.filter(s => MANUAL_SOURCES.includes(s.source)).reduce((t, s) => t + s.count, 0)
  const total = data.sources.reduce((t, s) => t + s.count, 0)
  const slices: Slice[] = data.sources.map(s => ({ key: s.source, label: <SourceTag source={s.source} label={SOURCE_META[s.source]?.label ?? s.label} />, text: s.label, value: s.count, colour: SOURCE_COLOURS[s.source] ?? SOURCE_COLOURS.other }))
  const best = Math.max(0, ...data.sources.map(s => s.count))
  const leaders = data.sources.filter(s => s.count === best)
  const mailTotal = data.accounts.reduce((t, a) => t + a.count, 0)
  const topMail = [...data.accounts].sort((a, b) => b.count - a.count)[0]
  return <>
    <MiniStats items={[
      { label: 'Email', value: count('email'), detail: `${data.accounts.length} account${data.accounts.length === 1 ? '' : 's'}`, colour: SOURCE_COLOURS.email, icon: Mail },
      { label: 'WhatsApp', value: count('whatsapp'), detail: 'Business number', colour: SOURCE_COLOURS.whatsapp, icon: MessageCircle },
      { label: 'Manual', value: manual, detail: 'Walk-in, referral and other', colour: SOURCE_COLOURS.walk_in, icon: Footprints },
      { label: 'Total added', value: total, detail: 'All sources', icon: Users },
    ]} />
    <div className="rv-pair">
      <ChartCard title="Candidates by source" hint="Share of new candidates from each source in this period." empty={!total} emptyHint="No candidates were added in this period. Adjust the dates or add a candidate." summary={`${total} candidates added.`}
        insight={best > 0 ? (leaders.length === 1 ? <><b>{leaders[0].label}</b> brought {num(best)} of {num(total)} {total === 1 ? 'candidate' : 'candidates'} ({pct(best, total)}%).</> : <><b>{leaders.map(s => s.label).join(' and ')}</b> brought {num(best)} each.</>) : undefined}>
        <Donut slices={slices} centre={total.toLocaleString()} centreLabel="candidates" />
      </ChartCard>
      <ChartCard title="Candidates by email account" hint="The email account that received the first message." empty={!data.accounts.length} emptyHint="No email account is connected. Connect one in Inbox to see emails here." summary={`${data.accounts.length} email accounts.`}
        insight={topMail && mailTotal > 0 ? <><b>{topMail.account}</b> received {num(topMail.count)} of {num(mailTotal)} email {mailTotal === 1 ? 'candidate' : 'candidates'} ({pct(topMail.count, mailTotal)}%).</> : 'No candidate arrived by email in this period.'}>
        <MailboxShare accounts={data.accounts} />
      </ChartCard>
    </div>
    <div className="rv-pair">
      <RTable rows={data.sources} rowKey={r => r.source} title="By source" columns={c => [
        c.col('label', 'Source', '50%', r => r.label, { render: r => <SourceTag source={r.source} label={r.label} /> }),
        c.num('count', 'Candidates', '25%', r => r.count), c.num('share', 'Share', '25%', r => pct(r.count, total), r => <span className="tnum">{pct(r.count, total)}%</span>)]} />
      <RTable rows={data.accounts} rowKey={r => r.account} title="By email account" columns={c => [c.col('account', 'Email account', '72%', r => r.account), c.num('count', 'Candidates', '28%', r => r.count)]} />
    </div>
  </>
}

/* ------------------------------------------------------------------ orders */

export function OrdersReport({ data, go }: { data: Data; go: Go }) {
  const store = useStore()
  const series: Series[] = [{ key: 'matched', name: 'Matched', colour: SERIES.primary }, { key: 'selected', name: 'Selected', colour: SERIES.violet }, { key: 'deployed', name: 'Deployed', colour: SERIES.teal }]
  const shown = data.orders.slice(0, 12)
  const openings = data.orders.reduce((t, o) => t + o.openings, 0)
  const deployed = data.orders.reduce((t, o) => t + o.deployed, 0)
  const filled = data.orders.filter(o => o.openings > 0 && o.deployed >= o.openings).length
  return <>
    <ChartCard title="Orders and how far each one is filled" legend={series} hint={data.orders.length > shown.length ? `Showing the first ${shown.length} of ${data.orders.length} orders; the table lists all.` : 'Each ladder is measured against the order’s openings: matched, then selected, then deployed.'} empty={!shown.length}
      emptyHint="No order matches these filters. Clear the client, order or job title filter." summary={`${data.orders.length} orders.`}
      insight={openings > 0 ? <><b>{num(deployed)} of {num(openings)}</b> openings are deployed across these orders; {plural(filled, 'order')} {filled === 1 ? 'is' : 'are'} fully filled.</> : 'These orders have no openings set.'}>
      <OrderLadders orders={shown} country={id => store.order(id)?.country} onOpen={id => go(`/orders/${id}`)} />
    </ChartCard>
    <RTable rows={data.orders} rowKey={r => r.id} onRowClick={r => go(`/orders/${r.id}`)} title="Order detail" caption="Select a row to open the order." stackBelow={1280}
      columns={c => [
        c.col('ref', 'Order', '12%', r => r.ref, { render: r => <span className="rv-order"><Flag country={store.order(r.id)?.country} /><b>{r.ref}</b></span> }),
        c.col('client', 'Client', '14%', r => r.client), c.col('company', 'Company', '17%', r => r.company), c.col('designation', 'Job title', '13%', r => r.designation),
        c.col('status', 'Status', '10%', r => r.status, { render: r => <StatusPill status={r.status} /> }),
        c.num('openings', 'Openings', '8%', r => r.openings), c.num('matched', 'Matched', '8%', r => r.matched), c.num('selected', 'Selected', '8%', r => r.selected),
        c.num('deployed', 'Deployed', '10%', r => r.deployed, r => <span className="rv-deployed"><span className="tnum">{r.deployed.toLocaleString()}</span><FillBar value={r.deployed} total={r.openings} tone="success" /></span>),
      ]} />
  </>
}

/* ------------------------------------------------------------------ rejections */

export function RejectionsReport({ data }: { data: Data }) {
  const names = new Map(data.pipeline.map(p => [p.stage, p.label]))
  const stageName = (stage: number) => `${stage}. ${names.get(stage) ?? 'Stage'}`
  const total = data.rejections.reduce((t, r) => t + r.count, 0)
  const sum = (reason: string) => data.rejections.filter(r => r.reason === reason).reduce((t, r) => t + r.count, 0)
  const named = ['prescreen_failed', 'interview_rejected', 'medical_unfit']
  const other = total - named.reduce((t, k) => t + sum(k), 0)
  const reasons = [...new Map(data.rejections.map(r => [r.reason, r.label])).entries()]
  const series: Series[] = reasons.map(([key, name]) => ({ key, name, colour: REASON_COLOURS[key] ?? SERIES.green }))
  const stages = [...new Set(data.rejections.map(r => r.stage))].sort((a, b) => a - b)
  const rows: ReasonRow[] = stages.map(stage => {
    const parts = reasons.map(([key, name]) => ({ key, name, value: data.rejections.find(r => r.stage === stage && r.reason === key)?.count ?? 0, colour: REASON_COLOURS[key] ?? SERIES.green }))
    return { stage, label: names.get(stage) ?? 'Stage', total: parts.reduce((t, p) => t + p.value, 0), parts }
  })
  const worst = rows.reduce<ReasonRow | null>((best, row) => (!best || row.total > best.total ? row : best), null)
  const topReason = [...reasons].map(([key, name]) => ({ name, value: sum(key) })).sort((a, b) => b.value - a.value)[0]
  return <>
    <MiniStats items={[
      { label: 'Prescreen failed', value: sum('prescreen_failed'), detail: 'Stage 1', colour: REASON_COLOURS.prescreen_failed, icon: UserX },
      { label: 'Interview rejected', value: sum('interview_rejected'), detail: 'Stage 4', colour: REASON_COLOURS.interview_rejected, icon: UserMinus },
      { label: 'Medical unfit', value: sum('medical_unfit'), detail: 'Stage 6', colour: REASON_COLOURS.medical_unfit, icon: HeartPulse },
      ...(other > 0 ? [{ label: 'Other reasons', value: other, detail: 'Not interested, resume missing and more', colour: SERIES.amber, icon: UserX }] : []),
      { label: 'Total ended', value: total, detail: 'All reasons, all stages', icon: Ban },
    ]} />
    <ChartCard title="Why applications ended, stage by stage" hint="One bar for each stage, split by the reason it ended." legend={series} empty={!total} emptyHint="No application ended in this period. They show here when a candidate fails a stage or drops out." summary={`${total} closures.`}
      insight={worst && topReason ? <>Most applications end at <b>Stage {worst.stage} · {worst.label}</b> ({num(worst.total)} of {num(total)}, {pct(worst.total, total)}%). Most common reason: <b>{topReason.name}</b> ({num(topReason.value)}).</> : undefined}>
      <ReasonBars rows={rows} grand={total} />
    </ChartCard>
    <RTable rows={data.rejections} rowKey={r => `${r.stage}:${r.reason}`} title="Details"
      columns={c => [
        c.col('stage', 'Stage', '34%', r => stageName(r.stage), { render: r => <StageChip stage={r.stage} label={names.get(r.stage) ?? 'Stage'} /> }),
        c.col('reason', 'Reason', '32%', r => r.label, { render: r => <span className="rv-reason" style={{ ['--tone' as string]: REASON_COLOURS[r.reason] ?? SERIES.green }}><i />{r.label}</span> }),
        c.num('count', 'Count', '14%', r => r.count), c.num('share', 'Share', '20%', r => pct(r.count, total), r => <span className="tnum">{pct(r.count, total)}%</span>),
      ]} />
  </>
}

/* ------------------------------------------------------------------ advance collections */

/** Months from the first to the last with collections, with the quiet months in between as zero, so the step line does not skip time. */
function monthRange(months: { month: string; amount: number }[]) {
  if (!months.length) return []
  const sorted = [...months].sort((a, b) => a.month.localeCompare(b.month))
  const [fy, fm] = sorted[0].month.split('-').map(Number)
  const [ly, lm] = sorted[sorted.length - 1].month.split('-').map(Number)
  const byMonth = new Map(sorted.map(item => [item.month, item.amount]))
  const out: { name: string; amount: number }[] = []
  for (let y = fy, m = fm; y < ly || (y === ly && m <= lm); m += 1) {
    if (m > 12) { m = 1; y += 1 }
    const key = `${y}-${String(m).padStart(2, '0')}`
    out.push({ name: fmtMonth(key), amount: byMonth.get(key) ?? 0 })
    if (y === ly && m === lm) break
  }
  return out
}

export function AdvanceReport({ data, go }: { data: Data; go: Go }) {
  const currencies = [...new Set([...data.advanceTotals.map(t => t.currency), ...data.advanceMonths.map(m => m.currency)])]
  return <>
    {data.advanceTotals.length
      ? <MiniStats items={data.advanceTotals.map(t => { const n = data.advances.filter(a => a.currency === t.currency).length; return { label: `Collected (${t.currency})`, value: fmtMoney(t.amount), detail: `${n} collection${n === 1 ? '' : 's'}`, colour: SERIES.primary, icon: Banknote } })} />
      : <p className="rv-quiet" role="status">No advance money was collected in this period. It shows here when stage 8 records an advance as collected.</p>}
    {currencies.length > 0 && <div className="rv-pair">
      {currencies.map(currency => {
        const months = monthRange(data.advanceMonths.filter(m => m.currency === currency).map(m => ({ month: m.month, amount: Number(m.amount) })))
        const best = months.reduce<typeof months[number] | null>((top, month) => (!top || month.amount > top.amount ? month : top), null)
        return <ChartCard key={currency} title={`Collected per month (${currency})`} hint="A step for each month. Each currency has its own chart." summary={`${months.length} months in ${currency}.`}
          insight={best && best.amount > 0 ? <>Highest month: <b>{best.name}</b> with {fmtMoney(best.amount)} {currency}.</> : undefined}>
          {months.length < 3 ? <MonthBars rows={months} unit={currency} /> : <StepArea data={months} unit={currency} label={`Collected per month in ${currency}`} />}
        </ChartCard>
      })}
    </div>}
    <RTable rows={data.advances} rowKey={r => r.id} onRowClick={r => go(`/candidates/${r.candidateId}`)} title="Collected per candidate" caption="Select a row to open the candidate."
      columns={c => [
        c.col('candidate', 'Candidate', '30%', r => r.candidate, { render: r => <RecordIdentity name={r.candidate} /> }), c.col('order', 'Order', '18%', r => r.order), c.col('date', 'Collected on', '20%', r => r.date, { render: r => fmtDate(r.date) }),
        c.col('currency', 'Currency', '12%', r => r.currency), c.num('amount', 'Amount', '20%', r => Number(r.amount), r => <span className="tnum">{fmtMoney(r.amount)}</span>),
      ]} />
    {data.advanceMonths.length > 0 && <RTable rows={data.advanceMonths} rowKey={r => `${r.month}:${r.currency}`} title="Collected per month" caption="Totals for each month and currency."
      columns={c => [
        c.col('month', 'Month', '40%', r => r.month, { render: r => fmtMonth(r.month) }), c.col('currency', 'Currency', '25%', r => r.currency), c.num('amount', 'Amount', '35%', r => Number(r.amount), r => <span className="tnum">{fmtMoney(r.amount)}</span>),
      ]} />}
  </>
}
