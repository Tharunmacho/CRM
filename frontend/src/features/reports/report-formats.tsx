import type { CSSProperties, ReactNode } from 'react'
import { ArrowUpRight } from 'lucide-react'
import { RecordIdentity } from '@/shared/ui/workspace'
import { StatusPill } from '@/shared/ui/display'
import type { OrderMetrics } from './report-api'
import { SERIES, fmtMoney, pct, type Pipeline } from './report-config'
import { Flag, StageChip } from './report-parts'

/**
 * The report chart formats. They are drawn with plain HTML and CSS (no chart library), so they are light,
 * follow the theme tokens, print well and read the same in the text alternative. Each one is built from
 * the shape of its data: a funnel for stages, a ranked list for people, ladders for progress against a target,
 * one stacked bar per stage for reasons.
 */

const num = (value: number) => value.toLocaleString()
const tone = (colour: string): CSSProperties => ({ ['--tone' as string]: colour })

/* ------------------------------------------------------------ tapered pipeline funnel */

type Drop = { lost: number; percent: number } | null

/** Eleven steps, each as wide as the applications that reached it, with the number lost before the next step between them. */
export function StepFunnel({ rows, dropOff }: { rows: Pipeline[]; dropOff: (index: number) => Drop }) {
  const top = Math.max(1, ...rows.map(row => row.reached))
  const width = (reached: number) => (reached <= 0 ? 0 : Math.max(2.5, reached / top * 100))
  const first = rows[0]?.reached ?? 0
  return <ol className="rv-sf" aria-label="Applications that reached each stage, with the number lost before the next stage">
    {rows.map((row, index) => {
      const drop = dropOff(index)
      const own = width(row.reached)
      const next = rows[index + 1] ? width(rows[index + 1].reached) : own * 0.82
      const inset = own > 0 ? Math.max(0, (1 - next / own) / 2 * 100) : 0
      const total = Math.max(1, row.active + row.progressed + row.closed)
      const a = row.active / total * 100, b = a + row.progressed / total * 100
      return <li key={row.stage}>
        <div className="rv-sf-step" title={`Stage ${row.stage}, ${row.label}: ${row.reached} reached (${row.active} active or waiting, ${row.progressed} moved on, ${row.closed} ended here)`}>
          <span className="rv-sf-name"><StageChip stage={row.stage} label={row.label} /></span>
          <span className="rv-sf-shape" aria-hidden="true">
            {own > 0 && <i style={{
              width: `${own}%`, clipPath: `polygon(0 0, 100% 0, ${100 - inset}% 100%, ${inset}% 100%)`,
              background: `linear-gradient(90deg, var(--rv-c1) 0 ${a}%, var(--rv-c-soft) ${a}% ${b}%, var(--rv-c-bad) ${b}% 100%)`,
            }} />}
          </span>
          <span className="rv-sf-count"><b className="tnum">{num(row.reached)}</b><small className="tnum">{first ? `${Math.round(row.reached / first * 100)}%` : ''}</small></span>
        </div>
        {drop && <div className="rv-sf-gap">
          <span className="rv-sf-link" aria-hidden="true" style={{ width: `${width(rows[index + 1].reached)}%` }} />
          <span className={`rv-sf-chip${drop.lost > 0 ? '' : ' is-none'}`}>
            <b className="tnum">{drop.lost > 0 ? `−${num(drop.lost)}` : '0'}</b>
            <small>{drop.lost > 0 ? `${drop.percent}% lost before the next stage` : 'none lost before the next stage'}</small>
          </span>
        </div>}
      </li>
    })}
  </ol>
}

/* ------------------------------------------------------------ ranked lollipop list */

export interface RankRow { key: string; name: string; value: number | null; label: string; sub?: string; person?: boolean; stage?: number }

/** A ranked list: rank, name, a stem with a dot at the value, and the number. Rows without a value stay, dimmed. */
export function Lollipops({ rows, colour, ranked = true, label }: { rows: RankRow[]; colour: string; ranked?: boolean; label: string }) {
  const max = Math.max(1, ...rows.map(row => row.value ?? 0))
  let place = 0
  return <ol className="rv-lp" aria-label={label} style={tone(colour)}>
    {rows.map((row, index) => {
      if (ranked && (index === 0 || rows[index - 1].value !== row.value)) place = index + 1
      const share = row.value == null ? 0 : row.value / max * 100
      return <li key={row.key} className={row.value == null ? 'is-none' : undefined}>
        <span className="rv-lp-rank tnum" aria-hidden={!ranked}>{ranked ? place : row.stage}</span>
        <span className="rv-lp-name">{row.person ? <RecordIdentity name={row.name} detail={row.sub} /> : <><strong>{row.name}</strong>{row.sub && <small>{row.sub}</small>}</>}</span>
        <span className="rv-lp-track" aria-hidden="true"><i className="rv-lp-stem" style={{ width: `${share}%` }} /><b className="rv-lp-dot" style={{ left: `${share}%` }} /></span>
        <span className="rv-lp-value tnum">{row.label}</span>
      </li>
    })}
  </ol>
}

/* ------------------------------------------------------------ mailbox share */

const MAILBOX_COLOURS = [SERIES.primary, SERIES.teal, SERIES.amber, SERIES.violet, SERIES.rose, SERIES.green]

/** One 100% bar for all email candidates, cut by mailbox, and a row per mailbox underneath. */
export function MailboxShare({ accounts }: { accounts: { account: string; count: number }[] }) {
  const total = accounts.reduce((sum, row) => sum + row.count, 0)
  const rows = [...accounts].sort((a, b) => b.count - a.count || a.account.localeCompare(b.account))
  const max = Math.max(1, ...rows.map(row => row.count))
  return <div className="rv-mb">
    <div className="rv-mb-stack" role="img" aria-label={`Email candidates by mailbox: ${rows.map(row => `${row.account} ${row.count}`).join(', ')}`}>
      {total > 0 ? rows.filter(row => row.count > 0).map(row => <i key={row.account} title={`${row.account}: ${num(row.count)} (${pct(row.count, total)}%)`}
        style={{ flexGrow: row.count, background: MAILBOX_COLOURS[rows.indexOf(row) % MAILBOX_COLOURS.length] }} />) : <i className="is-empty" />}
    </div>
    <ul>
      {rows.map((row, index) => <li key={row.account} className={row.count === 0 ? 'is-none' : undefined}>
        <span className="rv-mb-name" title={row.account}><i style={{ background: MAILBOX_COLOURS[index % MAILBOX_COLOURS.length] }} />{row.account}</span>
        <span className="rv-mb-track" aria-hidden="true"><i style={{ width: `${row.count / max * 100}%`, background: MAILBOX_COLOURS[index % MAILBOX_COLOURS.length] }} /></span>
        <b className="tnum">{num(row.count)}</b><em className="tnum">{pct(row.count, total)}%</em>
      </li>)}
    </ul>
  </div>
}

/* ------------------------------------------------------------ order ladders */

const RUNGS = [
  { key: 'matched', label: 'Matched', colour: SERIES.primary },
  { key: 'selected', label: 'Selected', colour: SERIES.violet },
  { key: 'deployed', label: 'Deployed', colour: SERIES.teal },
] as const

/** One card per order: openings are the full width of the ladder and each rung fills against them. */
export function OrderLadders({ orders, country, onOpen }: { orders: OrderMetrics[]; country: (id: string) => string | null | undefined; onOpen: (id: string) => void }) {
  return <ul className="rv-ladders">
    {orders.map(order => <li key={order.id} className="rv-ladder">
      <button type="button" className="rv-ladder-head" onClick={() => onOpen(order.id)} aria-label={`Open order ${order.ref}`}>
        <span className="rv-ladder-title"><Flag country={country(order.id)} /><b>{order.ref}</b><StatusPill status={order.status} /></span>
        <span className="rv-ladder-sub">{order.designation} · {order.company}</span>
        <ArrowUpRight className="rv-ladder-go" size={15} aria-hidden="true" />
      </button>
      <p className="rv-ladder-open"><b className="tnum">{num(order.openings)}</b> {order.openings === 1 ? 'opening' : 'openings'}</p>
      <dl>
        {RUNGS.map(rung => {
          const value = order[rung.key]
          const share = order.openings > 0 ? Math.min(100, value / order.openings * 100) : 0
          const over = order.openings > 0 && value > order.openings
          return <div key={rung.key} style={tone(rung.colour)} title={`${rung.label}: ${value} of ${order.openings} openings`}>
            <dt>{rung.label}</dt>
            <dd className="rv-rung"><span className="rv-rung-track" aria-hidden="true"><i style={{ width: `${share}%` }} />{over && <em />}</span>
              <b className="tnum">{num(value)}<small>/{num(order.openings)}</small></b>
              {over && <span className="rv-over">+{num(value - order.openings)} extra</span>}
            </dd>
          </div>
        })}
      </dl>
    </li>)}
  </ul>
}

/* ------------------------------------------------------------ reasons per stage */

export interface ReasonRow { stage: number; label: string; total: number; parts: { key: string; name: string; value: number; colour: string }[] }

/** One horizontal stacked bar per stage, cut by the reason the application ended. */
export function ReasonBars({ rows, grand }: { rows: ReasonRow[]; grand: number }) {
  const max = Math.max(1, ...rows.map(row => row.total))
  return <ol className="rv-rb" aria-label="Applications that ended, by stage and reason">
    {rows.map(row => <li key={row.stage} title={row.parts.filter(part => part.value > 0).map(part => `${part.name}: ${part.value}`).join(', ')}>
      <span className="rv-rb-name"><StageChip stage={row.stage} label={row.label} /></span>
      <span className="rv-rb-track" aria-hidden="true">
        <span className="rv-rb-bar" style={{ width: `${Math.max(3, row.total / max * 100)}%` }}>
          {row.parts.filter(part => part.value > 0).map(part => <i key={part.key} style={{ flexGrow: part.value, background: part.colour }}><span>{part.value}</span></i>)}
        </span>
      </span>
      <span className="rv-rb-total"><b className="tnum">{num(row.total)}</b><small className="tnum">{pct(row.total, grand)}%</small></span>
      <ul className="sr-only">{row.parts.filter(part => part.value > 0).map(part => <li key={part.key}>{part.name}: {part.value}</li>)}</ul>
    </li>)}
  </ol>
}

export function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { key: T; label: ReactNode }[]; onChange: (key: T) => void; label: string }) {
  return <div className="rv-seg" role="group" aria-label={label}>
    {options.map(option => <button key={option.key} type="button" aria-pressed={value === option.key} onClick={() => onChange(option.key)}>{option.label}</button>)}
  </div>
}


/** Few months of money: a bar per month says it better than a line with two points. */
export function MonthBars({ rows, unit, colour = SERIES.primary }: { rows: { name: string; amount: number }[]; unit: string; colour?: string }) {
  const max = Math.max(1, ...rows.map(row => row.amount))
  return <ol className="rv-mbars" style={tone(colour)} aria-label={`Collected per month in ${unit}`}>
    {rows.map(row => <li key={row.name}>
      <span className="rv-mbars-name">{row.name}</span>
      <span className="rv-mbars-track" aria-hidden="true"><i style={{ width: `${Math.max(2, row.amount / max * 100)}%` }} /></span>
      <b className="tnum">{fmtMoney(row.amount)}<small> {unit}</small></b>
    </li>)}
  </ol>
}
