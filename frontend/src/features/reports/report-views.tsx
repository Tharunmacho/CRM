import type { ReactNode } from 'react'
import { Ban, BarChart3, Banknote, Footprints, HeartPulse, Mail, MessageCircle, UserMinus, UserX, Users } from 'lucide-react'
import { DataTable, type Column } from '@/shared/ui/table'
import { EmptyState, StatusPill } from '@/shared/ui/display'
import { RecordIdentity } from '@/shared/ui/workspace'
import { useStore } from '@/shared/state/crm-store'
import type { ReportResult } from './report-api'
import { ChartCard, Columns, Donut, Funnel, HBars, MiniStats, useNarrow, type Series, type Slice } from './report-charts'
import { FillBar, Flag, SOURCE_META, SourceTag, StageChip } from './report-parts'
import { MANUAL_SOURCES, REASON_COLOURS, SERIES, SOURCE_COLOURS, fmtDate, fmtHours, fmtMonth, fmtMoney, stageDropOff } from './report-config'

type Data = ReportResult
type Go = (path: string) => void
type Stack = 768 | 1024 | 1280

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

const pct = (part: number, total: number) => total ? Math.round(part / total * 1000) / 10 : 0

export function FunnelReport({ data }: { data: Data }) {
  const rows = data.pipeline
  const series: Series[] = [{ key: 'active', name: 'Active or waiting', colour: SERIES.primary }, { key: 'progressed', name: 'Moved on', colour: SERIES.soft }, { key: 'closed', name: 'Ended here', colour: SERIES.bad }]
  const empty = rows.every(r => r.reached === 0)
  return <>
    <ChartCard title="Candidates per stage" hint="Each bar shows how many applications reached the stage, split by what happened next. The number on the right is how many were lost before the next stage." legend={series}
      summary={`${rows[0]?.reached ?? 0} applications reached stage 1 and ${rows[rows.length - 1]?.reached ?? 0} reached stage ${rows.length}.`} empty={empty}
      emptyHint="No candidate was matched to an order in this period. Match candidates to orders, or choose a longer date range.">
      <Funnel rows={rows} dropOff={index => stageDropOff(rows, index)} />
    </ChartCard>
    <RTable rows={rows} rowKey={r => String(r.stage)} title="Stage detail" caption="Lost counts applications that reached a stage but never reached the next one."
      columns={c => [
        c.col('stage', 'Stage', '25%', r => `${r.stage}. ${r.label}`, { render: r => <StageChip stage={r.stage} label={r.label} /> }),
        c.num('reached', 'Reached', '11%', r => r.reached), c.num('active', 'Active', '12%', r => r.active),
        c.num('progressed', 'Moved on', '13%', r => r.progressed), c.num('closed', 'Ended here', '13%', r => r.closed),
        c.num('closure', '% ended here', '12%', r => r.dropOffPercent, r => <span className="tnum">{r.dropOffPercent}%</span>),
        c.num('drop', 'Lost to next', '14%', r => stageDropOff(rows, rows.indexOf(r))?.lost, r => { const d = stageDropOff(rows, rows.indexOf(r)); return d ? <span className="tnum">{d.lost.toLocaleString()} <em className="rv-sub">({d.percent}%)</em></span> : <span className="rv-sub">Final stage</span> }),
      ]} />
  </>
}

export function StaffReport({ data }: { data: Data }) {
  const series: Series[] = [{ key: 'assigned', name: 'Candidates assigned', colour: SERIES.primary }, { key: 'completedTasks', name: 'Tasks finished', colour: SERIES.teal }, { key: 'escalations', name: 'Sent to admin', colour: SERIES.amber }, { key: 'missedUpdates', name: 'Missed daily updates', colour: SERIES.rose }]
  const hoursSeries: Series[] = [{ key: 'averageHours', name: 'Average hours', colour: SERIES.primary }]
  const noStaff = !data.staff.length
  const noHours = data.stageHours.every(s => s.averageHours == null)
  return <>
    <div className="rv-pair">
      <ChartCard title="Workload by staff member" hint="Candidates given, tasks finished, tasks sent to the admin and missed daily updates." legend={series} empty={noStaff}
        emptyHint="No staff match these filters." summary={`${data.staff.length} staff members.`}>
        <Columns data={data.staff.map(s => ({ ...s, name: s.name.split(' ')[0] }))} category="name" series={series} valueLabel="Count" categoryLabel="Staff" height={310} barMax={22} />
      </ChartCard>
      <ChartCard title="Average hours per stage" hint="Time from when a task is created to when its stage is finished, across the staff shown." legend={hoursSeries} empty={noHours}
        emptyHint="No stage was finished in this period, so there is no average time to show." summary="Average completion time for each of the 11 stages.">
        <HBars data={data.stageHours.map(s => ({ ...s, name: `${s.stage}. ${s.label}` }))} category="name" series={hoursSeries} valueLabel="Hours" unit="h" labelWidth={190} height={360} />
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

export function SourceReport({ data }: { data: Data }) {
  const count = (key: string) => data.sources.find(s => s.source === key)?.count ?? 0
  const manual = data.sources.filter(s => MANUAL_SOURCES.includes(s.source)).reduce((t, s) => t + s.count, 0)
  const total = data.sources.reduce((t, s) => t + s.count, 0)
  const series: Series[] = [{ key: 'count', name: 'Candidates', colour: SERIES.primary }]
  const slices: Slice[] = data.sources.map(s => ({ key: s.source, label: <SourceTag source={s.source} label={SOURCE_META[s.source]?.label ?? s.label} />, text: s.label, value: s.count, colour: SOURCE_COLOURS[s.source] ?? SOURCE_COLOURS.other }))
  return <>
    <MiniStats items={[
      { label: 'Email', value: count('email'), detail: `${data.accounts.length} account${data.accounts.length === 1 ? '' : 's'}`, colour: SOURCE_COLOURS.email, icon: Mail },
      { label: 'WhatsApp', value: count('whatsapp'), detail: 'Business number', colour: SOURCE_COLOURS.whatsapp, icon: MessageCircle },
      { label: 'Manual', value: manual, detail: 'Walk-in, referral and other', colour: SOURCE_COLOURS.walk_in, icon: Footprints },
      { label: 'Total added', value: total, detail: 'All sources', icon: Users },
    ]} />
    <div className="rv-pair">
      <ChartCard title="Candidates by source" hint="Share of new candidates from each source in this period." empty={!total} emptyHint="No candidates were added in this period. Adjust the dates or add a candidate." summary={`${total} candidates added.`}>
        <Donut slices={slices} centre={total.toLocaleString()} centreLabel="candidates" />
      </ChartCard>
      <ChartCard title="Candidates by email account" hint="The email account that received the first message." empty={!data.accounts.length} emptyHint="No email account is connected. Connect one in Inbox to see emails here." summary={`${data.accounts.length} email accounts.`}>
        <HBars data={data.accounts} category="account" series={series} valueLabel="Candidates" labelWidth={210} height={Math.max(200, data.accounts.length * 40 + 64)} />
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

export function OrdersReport({ data, go }: { data: Data; go: Go }) {
  const store = useStore()
  const narrow = useNarrow()
  const series: Series[] = [{ key: 'openings', name: 'Openings', colour: SERIES.primary }, { key: 'matched', name: 'Matched', colour: SERIES.amber }, { key: 'selected', name: 'Selected', colour: SERIES.teal }, { key: 'deployed', name: 'Deployed', colour: SERIES.violet }]
  // Chart rows must not carry a `ref` key: recharts spreads each row into the bar props and React would treat it as a React ref.
  const shown = data.orders.slice(0, 12).map(({ ref: order, openings, matched, selected, deployed }) => ({ order, openings, matched, selected, deployed }))
  return <>
    <ChartCard title="Open positions and progress, for each order" hint={data.orders.length > shown.length ? `Showing the first ${shown.length} of ${data.orders.length} orders; the table lists all.` : 'Openings are how many people the order needs. The other bars count candidates matched to the order.'} legend={series} empty={!shown.length}
      emptyHint="No order matches these filters. Clear the client, order or job title filter." summary={`${data.orders.length} orders.`}>
      <div className={narrow ? 'rv-hscroll' : undefined}><div style={narrow ? { minWidth: Math.max(420, shown.length * 84) } : undefined}>
        <Columns data={shown} category="order" series={series} valueLabel="Candidates / openings" categoryLabel="Order" height={320} barMax={16} />
      </div></div>
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

export function RejectionsReport({ data }: { data: Data }) {
  const names = new Map(data.pipeline.map(p => [p.stage, p.label]))
  const stageName = (stage: number) => `${stage}. ${names.get(stage) ?? 'Stage'}`
  const total = data.rejections.reduce((t, r) => t + r.count, 0)
  const sum = (reason: string) => data.rejections.filter(r => r.reason === reason).reduce((t, r) => t + r.count, 0)
  const reasons = [...new Map(data.rejections.map(r => [r.reason, r.label])).entries()]
  const series: Series[] = reasons.map(([key, name]) => ({ key, name, colour: REASON_COLOURS[key] ?? SERIES.green }))
  const stages = [...new Set(data.rejections.map(r => r.stage))].sort((a, b) => a - b)
  const chart = stages.map(stage => ({ name: stageName(stage), ...Object.fromEntries(reasons.map(([key]) => [key, data.rejections.find(r => r.stage === stage && r.reason === key)?.count ?? 0])) }))
  return <>
    <MiniStats items={[
      { label: 'Prescreen failed', value: sum('prescreen_failed'), detail: 'Stage 1', colour: REASON_COLOURS.prescreen_failed, icon: UserX },
      { label: 'Interview rejected', value: sum('interview_rejected'), detail: 'Stage 4', colour: REASON_COLOURS.interview_rejected, icon: UserMinus },
      { label: 'Medical unfit', value: sum('medical_unfit'), detail: 'Stage 6', colour: REASON_COLOURS.medical_unfit, icon: HeartPulse },
      { label: 'Total ended', value: total, detail: 'All reasons, all stages', icon: Ban },
    ]} />
    <ChartCard title="Ended applications by stage and reason" hint="How many applications ended in this period." legend={series} empty={!total} emptyHint="No application ended in this period. They show here when a candidate fails a stage or drops out." summary={`${total} closures.`}>
      <HBars data={chart} category="name" series={series} stacked valueLabel="Applications" labelWidth={190} />
    </ChartCard>
    <RTable rows={data.rejections} rowKey={r => `${r.stage}:${r.reason}`} title="Details"
      columns={c => [
        c.col('stage', 'Stage', '34%', r => stageName(r.stage), { render: r => <StageChip stage={r.stage} label={names.get(r.stage) ?? 'Stage'} /> }),
        c.col('reason', 'Reason', '32%', r => r.label, { render: r => <span className="rv-reason" style={{ ['--tone' as string]: REASON_COLOURS[r.reason] ?? SERIES.green }}><i />{r.label}</span> }),
        c.num('count', 'Count', '14%', r => r.count), c.num('share', 'Share', '20%', r => pct(r.count, total), r => <span className="tnum">{pct(r.count, total)}%</span>),
      ]} />
  </>
}

export function AdvanceReport({ data, go }: { data: Data; go: Go }) {
  const currencies = [...new Set([...data.advanceTotals.map(t => t.currency), ...data.advanceMonths.map(m => m.currency)])]
  const series: Series[] = [{ key: 'amount', name: 'Collected', colour: SERIES.primary }]
  return <>
    {data.advanceTotals.length
      ? <MiniStats items={data.advanceTotals.map(t => { const n = data.advances.filter(a => a.currency === t.currency).length; return { label: `Collected (${t.currency})`, value: fmtMoney(t.amount), detail: `${n} collection${n === 1 ? '' : 's'}`, colour: SERIES.primary, icon: Banknote } })} />
      : <p className="rv-quiet" role="status">No advance money was collected in this period. It shows here when stage 8 records an advance as collected.</p>}
    {currencies.length > 0 && <div className="rv-pair">
      {currencies.map(currency => {
        const months = data.advanceMonths.filter(m => m.currency === currency).map(m => ({ name: fmtMonth(m.month), amount: Number(m.amount) })).reverse()
        return <ChartCard key={currency} title={`Collected per month (${currency})`} hint="Each currency has its own chart." legend={series} summary={`${months.length} months with collections in ${currency}.`}>
          <Columns data={months} category="name" series={series} valueLabel={`Amount (${currency})`} categoryLabel="Month" height={280} unit={currency} money barMax={44} />
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
