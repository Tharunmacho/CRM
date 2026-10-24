import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Check, CheckCircle2, Copy, FileText, FileWarning, Globe2, LayoutGrid, MoreHorizontal, Rows3 } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { initials } from '@/shared/lib/format'
import { STAGES, TOTAL_STAGES } from '@/shared/workflow/stages'
import type { Application, Company, Order } from '@/shared/model/types'
import '@/styles/clients.css'

/**
 * Care India's own touches for the client side: country flags, soft hue avatars, the 11-stage
 * mini tracker, the deployed/openings progress and the list/cards switch. Styles: styles/clients.css.
 */

/* ----------------------------------------------------------------- flags */

// Short names must match exactly (so "UK" never matches inside another word); long names may be contained.
const EXACT: [string, string[]][] = [
  ['ae', ['uae', 'u.a.e', 'u.a.e.']],
  ['sa', ['ksa']],
  ['gb', ['uk', 'u.k', 'u.k.']],
  ['us', ['us', 'usa', 'u.s.a']],
  ['in', ['in']],
]
const CONTAINS: [string, string[]][] = [
  ['ae', ['united arab emirates', 'emirates', 'dubai', 'abu dhabi', 'sharjah']],
  ['sa', ['saudi']],
  ['qa', ['qatar']],
  ['kw', ['kuwait']],
  ['om', ['oman']],
  ['bh', ['bahrain']],
  ['de', ['germany']],
  ['gb', ['united kingdom', 'britain', 'england']],
  ['in', ['india']],
  ['sg', ['singapore']],
  ['us', ['united states', 'america']],
]

export function countryCode(country?: string | null) {
  const key = (country ?? '').trim().toLowerCase()
  if (!key) return null
  return EXACT.find(([, names]) => names.includes(key))?.[0] ?? CONTAINS.find(([, names]) => names.some((name) => key.includes(name)))?.[0] ?? null
}

/** Flag from /flags, or a globe when the country is not one we ship (or none is recorded). */
export function Flag({ country, className }: { country?: string | null; className?: string }) {
  const code = countryCode(country)
  return code
    ? <img src={`/flags/${code}.svg`} alt="" width={20} height={14} className={cn('cl-flag', className)} loading="lazy" />
    : <Globe2 size={15} aria-hidden="true" className={cn('cl-flag-globe', className)} />
}

/** Flag, country and (muted) city, as one block. */
export function Place({ country, city, empty = 'Not added', stacked }: { country?: string | null; city?: string | null; empty?: string; stacked?: boolean }) {
  if (!country && !city) return <span className="cl-quiet">{empty}</span>
  if (stacked) {
    return (
      <span className="cl-place is-stacked">
        <Flag country={country} />
        <span className="cl-place-text"><b title={country ?? undefined}>{country || city}</b>{country && city && <small title={city}>{city}</small>}</span>
      </span>
    )
  }
  return <span className="cl-place"><Flag country={country} /><span className="cl-place-text" title={[city, country].filter(Boolean).join(', ')}>{[city, country].filter(Boolean).join(', ')}</span></span>
}

/* --------------------------------------------------------------- avatars */

export const hueOf = (name: string) => name.split('').reduce((total, character) => total + character.charCodeAt(0), 0) % 6

export function Avatar({ name, size = 'md', className }: { name: string; size?: 'sm' | 'md' | 'lg' | 'xl'; className?: string }) {
  return <span className={cn('record-avatar cl-avatar', size !== 'md' && `is-${size}`, `avatar-hue-${hueOf(name || '?')}`, className)} aria-hidden="true">{initials(name || '?')}</span>
}

/* ----------------------------------------------------------- stage tracker */

/** Where an application stands among the eleven stages: a segment per stage, the number and name beside it. */
export function StageMini({ no, state }: { no: number; state: 'live' | 'closed' | 'deployed' }) {
  const stage = STAGES[Math.max(1, Math.min(TOTAL_STAGES, no)) - 1]
  return (
    <span className={cn('cl-track', `is-${state}`)} title={`Stage ${no} of ${TOTAL_STAGES}: ${stage.title}${state === 'closed' ? ' (closed)' : state === 'deployed' ? ' (deployed)' : ''}`}>
      <span className="cl-track-bar" aria-hidden="true">
        {STAGES.map((item) => <i key={item.no} className={cn(item.no < no && 'is-done', item.no === no && 'is-now', state === 'deployed' && 'is-done')} />)}
      </span>
      <span className="cl-track-label"><b className="tnum">{no}</b><span>{stage.title}</span></span>
    </span>
  )
}

/* ------------------------------------------------------------- pills */

/** Soft pill for the agreement: on file, partly on file or missing. */
export function AgreementPill({ filed, total, short }: { filed: number; total: number; short?: boolean }) {
  if (!total) return <span className="cl-quiet">No company yet</span>
  if (filed === total) {
    return <span className="cl-pill is-ok" title={total === 1 ? 'Agreement uploaded' : `All ${total} agreements uploaded`}><CheckCircle2 size={13} aria-hidden="true" />{short || total === 1 ? 'Uploaded' : `All ${total} uploaded`}</span>
  }
  return <span className="cl-pill is-warn" title={filed ? `${filed} of ${total} agreements uploaded` : 'No agreement uploaded'}><FileWarning size={13} aria-hidden="true" />{filed ? `${filed} of ${total} uploaded` : 'Missing'}</span>
}

export function FileChip({ name }: { name: string }) {
  return <span className="cl-file"><FileText size={14} aria-hidden="true" /><span title={name}>{name}</span></span>
}

/* ------------------------------------------------------------ demand */

export interface Demand { orders: number; openings: number; deployed: number; toFill: number }

/** Open orders, their openings and the seats already deployed, per client (through the companies it owns). */
export function demandByClient(companies: Company[], orders: Order[], applications: Application[]) {
  const owner = new Map(companies.map((company) => [company.id, company.clientId]))
  const deployedPerOrder = new Map<string, number>()
  for (const application of applications) {
    if (application.status === 'deployed') deployedPerOrder.set(application.orderId, (deployedPerOrder.get(application.orderId) ?? 0) + 1)
  }
  const byClient = new Map<string, Demand>()
  for (const order of orders) {
    if (order.status !== 'open') continue
    const clientId = owner.get(order.companyId)
    if (!clientId) continue
    const entry = byClient.get(clientId) ?? { orders: 0, openings: 0, deployed: 0, toFill: 0 }
    const placed = Math.min(deployedPerOrder.get(order.id) ?? 0, order.openings)
    entry.orders += 1
    entry.openings += order.openings
    entry.deployed += placed
    entry.toFill += order.openings - placed
    byClient.set(clientId, entry)
  }
  return byClient
}

/** Deployed against openings as a labelled bar. */
export function Progress({ done, total, label, className }: { done: number; total: number; label?: ReactNode; className?: string }) {
  const share = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0
  return (
    <span className={cn('cl-progress', className)} role="img" aria-label={`${done} of ${total} openings deployed`}>
      <span className="cl-bar" aria-hidden="true"><i style={{ width: `${share}%` }} /></span>
      {label !== undefined && <small>{label}</small>}
    </span>
  )
}

/** Open-orders cell: the count, the deployed/openings bar and a caption. */
export function DemandCell({ demand, empty }: { demand?: Demand; empty: ReactNode }) {
  if (!demand) return <span className="cl-demand is-idle"><span className="cl-quiet">No open orders</span><small>{empty}</small></span>
  return (
    <span className="cl-demand">
      <b className="tnum">{demand.orders} {demand.orders === 1 ? 'order' : 'orders'}</b>
      <Progress done={demand.deployed} total={demand.openings} />
      <small className="tnum">{demand.deployed} of {demand.openings} deployed</small>
    </span>
  )
}

/* --------------------------------------------------------------- controls */

/** Copies a value and confirms with a tick for a moment. */
export function CopyButton({ value, label }: { value: string; label: string }) {
  const [done, setDone] = useState(false)
  useEffect(() => {
    if (!done) return
    const timer = window.setTimeout(() => setDone(false), 1600)
    return () => window.clearTimeout(timer)
  }, [done])
  return (
    <button
      type="button"
      className="cl-copy"
      title={done ? 'Copied' : label}
      aria-label={label}
      onClick={(event) => {
        event.stopPropagation()
        void navigator.clipboard?.writeText(value).then(() => setDone(true), () => undefined)
      }}
    >
      {done ? <Check size={13} /> : <Copy size={13} />}
    </button>
  )
}

export type View = 'list' | 'cards'

/** The saved list/cards choice for a screen; defaults to the list. */
export function useSavedView(key: string): [View, (view: View) => void] {
  const [view, setView] = useState<View>(() => {
    try { return localStorage.getItem(key) === 'cards' ? 'cards' : 'list' } catch { return 'list' }
  })
  const save = (next: View) => {
    setView(next)
    try { localStorage.setItem(key, next) } catch { /* private window */ }
  }
  return [view, save]
}

export function ViewToggle({ view, onChange }: { view: View; onChange: (view: View) => void }) {
  return (
    <div className="cl-view" role="group" aria-label="Layout">
      <button type="button" aria-pressed={view === 'list'} title="List" aria-label="List view" onClick={() => onChange('list')}><Rows3 size={16} /></button>
      <button type="button" aria-pressed={view === 'cards'} title="Cards" aria-label="Card view" onClick={() => onChange('cards')}><LayoutGrid size={16} /></button>
    </div>
  )
}

/** True while the viewport matches the media query (re-evaluated on resize). */
export function useMediaQuery(query: string) {
  const [matches, setMatches] = useState(() => typeof window !== 'undefined' && !!window.matchMedia && window.matchMedia(query).matches)
  useEffect(() => {
    if (!window.matchMedia) return
    const list = window.matchMedia(query)
    const update = () => setMatches(list.matches)
    update()
    list.addEventListener('change', update)
    return () => list.removeEventListener('change', update)
  }, [query])
  return matches
}

export interface MenuEntry { label: string; icon?: ReactNode; danger?: boolean; onSelect: () => void }

/**
 * A "..." overflow menu for row actions. The list is drawn in a portal with fixed positioning, so a
 * table's scroll box can never clip it, and it closes on outside click, Escape, scroll and resize.
 */
export function RowMenu({ label, items }: { label: string; items: MenuEntry[] }) {
  const trigger = useRef<HTMLButtonElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [at, setAt] = useState({ top: 0, left: 0 })
  useLayoutEffect(() => {
    if (!open || !trigger.current) return
    const box = trigger.current.getBoundingClientRect()
    const width = 196
    const height = items.length * (window.innerWidth <= 640 ? 44 : 38) + 10
    const below = box.bottom + 6 + height <= window.innerHeight - 8
    setAt({ top: below ? box.bottom + 6 : Math.max(8, box.top - 6 - height), left: Math.max(8, Math.min(box.right - width, window.innerWidth - width - 8)) })
  }, [open, items.length])
  useEffect(() => {
    if (!open) return
    const close = () => setOpen(false)
    const onDown = (event: MouseEvent) => {
      const target = event.target as Node
      if (!trigger.current?.contains(target) && !list.current?.contains(target)) close()
    }
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') { close(); trigger.current?.focus() } }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    window.addEventListener('resize', close)
    window.addEventListener('scroll', close, true)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('resize', close)
      window.removeEventListener('scroll', close, true)
    }
  }, [open])
  return (
    <>
      <button ref={trigger} type="button" className="cl-menu-btn" title={label} aria-label={label} aria-haspopup="menu" aria-expanded={open} onClick={(event) => { event.stopPropagation(); setOpen((value) => !value) }}>
        <MoreHorizontal size={16} />
      </button>
      {open && createPortal(
        <div ref={list} role="menu" className="cl-menu" style={{ top: at.top, left: at.left }} onClick={(event) => event.stopPropagation()}>
          {items.map((item) => (
            <button key={item.label} type="button" role="menuitem" className={item.danger ? 'is-danger' : undefined} onClick={() => { setOpen(false); item.onSelect() }}>
              {item.icon}{item.label}
            </button>
          ))}
        </div>,
        document.body,
      )}
    </>
  )
}

/* ----------------------------------------------------------------- forms */

/** A titled block of a dialog form: a soft icon circle, a title and a one-line hint, then the fields. */
export function FormSection({ icon, title, hint, children, cols = 2 }: { icon: ReactNode; title: string; hint?: string; children: ReactNode; cols?: 1 | 2 }) {
  return (
    <section className="client-form-section">
      <header>
        <span aria-hidden="true">{icon}</span>
        <div><h3>{title}</h3>{hint && <p>{hint}</p>}</div>
      </header>
      <div className={cols === 2 ? 'grid gap-4 sm:grid-cols-2' : 'grid gap-4'}>{children}</div>
    </section>
  )
}

/** Country names offered while typing, from the records on screen plus the destinations we ship flags for. */
export const COUNTRY_SUGGESTIONS = ['United Arab Emirates', 'Saudi Arabia', 'Qatar', 'Kuwait', 'Oman', 'Bahrain', 'Germany', 'United Kingdom', 'India', 'Singapore', 'United States']

/* -------------------------------------------------------------------- CSV */

const csvCell = (value: unknown) => {
  let text = value == null ? '' : String(value)
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`
  return `"${text.replace(/"/g, '""')}"`
}

/** Downloads rows already on screen as a CSV file (no request is made). */
export function downloadCsv(filename: string, header: string[], rows: unknown[][]) {
  const body = [header, ...rows].map((row) => row.map(csvCell).join(',')).join('\r\n')
  const url = URL.createObjectURL(new Blob(['﻿' + body], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
