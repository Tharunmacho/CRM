import { Fragment, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { ArrowRight, CheckCircle2, ExternalLink, MoreHorizontal, PhoneCall, Save, UserRound } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { countryCode, flagSrc } from '@/shared/lib/country-flag'
import { Button, Textarea } from '@/shared/ui/primitives'
import { RecordIdentity } from '@/shared/ui/workspace'
import { STAGES, TOTAL_STAGES } from '@/shared/workflow/stages'
import {
  clientCell, clockTime, countdownLabel, countryCell, countryShort, countryTone, dueTone, msUntil, positionCell, postponedLabel, referralLabel, sessionLabel,
  type DailyItem,
} from '@/features/daily-work/daily-work-model'
import { OwnerTag } from './task-identity'

export interface DailyGroup { key: string; title: string; hint: string; icon: ReactNode; items: DailyItem[]; tone?: 'late' }

/** Column order and pixel widths (the table scrolls sideways below their sum; the staff column is for supervisors). */
const COLUMNS = [
  { key: 'no', title: 'S.No', width: 44 },
  { key: 'client', title: 'Client', width: 98 },
  { key: 'country', title: 'Country', width: 108 },
  { key: 'candidate', title: 'Candidate', width: 146, left: true },
  { key: 'position', title: 'Job', width: 92 },
  { key: 'referral', title: 'Referral', width: 82 },
  { key: 'staff', title: 'Staff', width: 96, supervisor: true },
  { key: 'stage', title: 'Stage', width: 120 },
  { key: 'remarks', title: 'Remarks', width: 146, left: true },
  { key: 'deadline', title: 'Deadline', width: 120 },
  { key: 'actions', title: 'Actions', width: 128 },
] as const

/** Wraps a wide table: a soft shadow shows on whichever side still has columns scrolled out of view. */
export function ScrollEdges({ children, label = 'Daily candidates, scroll sideways for more columns' }: { children: ReactNode; label?: string }) {
  const box = useRef<HTMLDivElement>(null)
  const [edges, setEdges] = useState({ left: false, right: false })
  useEffect(() => {
    const el = box.current
    if (!el) return
    const measure = () => setEdges((previous) => {
      const next = { left: el.scrollLeft > 2, right: el.scrollLeft + el.clientWidth < el.scrollWidth - 2 }
      return previous.left === next.left && previous.right === next.right ? previous : next
    })
    measure()
    el.addEventListener('scroll', measure, { passive: true })
    const watcher = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    watcher?.observe(el)
    if (el.firstElementChild) watcher?.observe(el.firstElementChild)
    return () => { el.removeEventListener('scroll', measure); watcher?.disconnect() }
  }, [])
  return (
    <div className={cn('dw-scroll', edges.left && 'has-left', edges.right && 'has-right')}>
      <div ref={box} className="tk-table-wrap" tabIndex={0} role="region" aria-label={label}>{children}</div>
    </div>
  )
}

export interface DailyTableProps {
  groups: DailyGroup[]
  now: number
  skew: number
  zone?: string
  supervisor: boolean
  staffName: (id: string) => string | undefined
  canWork: (item: DailyItem) => boolean
  onOpen: (item: DailyItem) => void
  onProfile: (item: DailyItem) => void
  onRemark: (item: DailyItem, text: string) => Promise<void>
}

/** The daily list in the reference style: centred columns, hairline dividers, 76px rows, banded Postponed/Morning/Evening groups. */
export function DailyTable({ groups, now, skew, zone, supervisor, staffName, canWork, onOpen, onProfile, onRemark }: DailyTableProps) {
  const columns = COLUMNS.filter((c) => supervisor || !('supervisor' in c))
  let serial = 0
  return (
    <ScrollEdges>
      <table className="dw-table dw-main" aria-label="Daily candidates" style={{ minWidth: columns.reduce((sum, c) => sum + c.width, 0) }}>
        <colgroup>{columns.map((c) => <col key={c.key} style={{ width: c.width }} />)}</colgroup>
        <thead><tr>{columns.map((c) => <th key={c.key} scope="col" className={cn('left' in c && 'is-left')}>{c.title}</th>)}</tr></thead>
        <tbody>
          {groups.map((g) => <Fragment key={g.key}>
            <tr className={cn('dw-group', g.tone === 'late' && 'is-late')}>
              <th colSpan={columns.length} scope="colgroup">
                <span className="dw-group-title">{g.icon}{g.title}<b className="tnum">{g.items.filter((i) => i.status === 'open').length} open</b></span>
                <small>{g.hint}</small>
              </th>
            </tr>
            {g.items.map((item) => {
              serial += 1
              return <ItemRow key={item.id} item={item} serial={serial} now={now} skew={skew} zone={zone} showStaff={supervisor}
                staffName={staffName(item.ownerId)} canWork={canWork(item)} onOpen={() => onOpen(item)} onProfile={() => onProfile(item)}
                onRemark={(text) => onRemark(item, text)} />
            })}
          </Fragment>)}
        </tbody>
      </table>
    </ScrollEdges>
  )
}

function ItemRow({ item, serial, now, skew, zone, showStaff, staffName, canWork, onOpen, onProfile, onRemark }: {
  item: DailyItem; serial: number; now: number; skew: number; zone?: string; showStaff: boolean; staffName?: string
  canWork: boolean; onOpen: () => void; onProfile: () => void; onRemark: (text: string) => Promise<void>
}) {
  const tone = dueTone(item, now, skew)
  const done = item.status === 'done'
  const left = msUntil(item.dueAt, now, skew)
  const due = clockTime(item.dueAt, zone)
  const action = done ? 'View' : item.applicationId ? 'Complete' : 'Open'
  return (
    <tr className={cn('dw-row', `is-${tone}`, item.postponed && 'is-postponed', serial % 2 === 0 && 'is-alt')}>
      <td className="is-num tnum" data-label="S.No">{serial}</td>
      <td data-label="Client"><ClientCell item={item} /></td>
      <td data-label="Country"><CountryCell item={item} onAdd={onProfile} /></td>
      <td data-label="Candidate" className="is-left"><RecordIdentity name={item.candidate} detail={item.candidateRef} /></td>
      <td data-label="Job"><PositionCellView item={item} /></td>
      <td data-label="Referral"><span className={cn('dw-ref', item.referral.source === 'referral' && 'is-referral')}>{referralLabel(item.referral)}</span></td>
      {showStaff && <td data-label="Staff">{staffName ? <OwnerTag name={staffName} /> : <span className="dw-none">Not assigned</span>}</td>}
      <td data-label="Stage"><StageCell item={item} /></td>
      <td data-label="Remarks" className="is-left"><RemarkCell item={item} canWork={canWork && !done} onSave={onRemark} /></td>
      <td data-label="Deadline">
        <div className={cn('dw-slot', `is-${tone}`)} title={`${item.postponed ? 'Was due' : 'Due'} ${due}`}>
          <span className="dw-slot-label">{item.postponed ? postponedLabel(item.daysLate) : `${sessionLabel(item.session)} ${item.slotNo} · ${due}`}</span>
          {done
            ? <b><CheckCircle2 size={14} aria-hidden="true" />Done {item.completedAt ? clockTime(item.completedAt, zone) : ''}</b>
            : <b className="tnum" aria-live="off">{countdownLabel(left)}</b>}
        </div>
      </td>
      <td data-label="Actions" className="is-actions">
        <div className="dw-actions">
          <Button size="sm" variant={done ? 'secondary' : 'primary'} className="dw-act" onClick={onOpen} aria-label={`${action === 'Complete' ? 'Complete the stage for' : action} ${item.candidate}`}>
            {action}
          </Button>
          <RowMenu candidate={item.candidate} items={[
            ...(item.applicationId ? [{ label: done ? 'View stage' : 'Open stage', icon: ArrowRight, onSelect: onOpen }] : []),
            { label: 'Open candidate profile', icon: UserRound, onSelect: onProfile },
          ]} />
        </div>
      </td>
    </tr>
  )
}

/** Client has no fallback: it exists only through an order. Say so instead of leaving a bare dash. */
function ClientCell({ item }: { item: DailyItem }) {
  const cell = clientCell(item)
  if (!cell.text) return <div className="dw-stack" title={cell.hint}><span className="dw-none">—</span><small className="dw-sub is-soft">No order yet</small></div>
  return (
    <div className="dw-stack" title={item.clientContact ? `Contact: ${item.clientContact}` : undefined}>
      <span className="dw-cell-strong">{cell.text}</span>{item.orderRef && <small className="dw-sub">{item.orderRef}</small>}
    </div>
  )
}

/** Solid country pill (flag inside when the app has one); the candidate's own wishes are marked "pref." underneath. */
function CountryCell({ item, onAdd }: { item: DailyItem; onAdd: () => void }) {
  const cell = countryCell(item, countryCode)
  if (cell.names.length === 0) {
    return (
      <div className="dw-stack" title={cell.hint}>
        <span className="dw-none">—</span>
        <small className="dw-sub is-soft">Not saved · <button type="button" className="dw-add" onClick={onAdd} aria-label={`Add a preferred country for ${item.candidate}`}>Add</button></small>
      </div>
    )
  }
  const [first, ...more] = cell.names
  const code = countryCode(first)
  const shown = countryShort(first)
  const note = cell.source === 'preference' ? 'pref.' : cell.source === 'location' ? 'from location' : ''
  return (
    <div className="dw-stack" title={`${cell.names.join(', ')}. ${cell.hint}`}>
      <span className="dw-pills">
        <span className={cn('dw-ctry', `dw-ctry-${countryTone(first)}`, cell.source !== 'order' && 'is-soft')}>
          {code && <img src={flagSrc(code)} alt="" width={16} height={11} loading="lazy" onError={(e) => { e.currentTarget.style.display = 'none' }} />}
          <span>{shown}</span>
        </span>
        {more.length > 0 && <small className="dw-more">+{more.length}</small>}
      </span>
      {note && <small className="dw-pref">{note}</small>}
    </div>
  )
}

function PositionCellView({ item }: { item: DailyItem }) {
  const cell = positionCell(item)
  if (!cell.text) {
    return <div className="dw-stack" title={cell.hint}><span className="dw-none">—</span><small className="dw-sub is-soft">Not saved</small></div>
  }
  return (
    <div className="dw-stack" title={`${cell.text}. ${cell.hint}`}>
      <span className="dw-cell-strong dw-clamp">{cell.text}</span>
      {cell.source === 'preference' && <small className="dw-pref">pref.</small>}
      {cell.source === 'skill' && <small className="dw-pref">from skills</small>}
    </div>
  )
}

/** A compact stage chip, the stage name in one muted line and a slim progress bar underneath (never over the text). */
function StageCell({ item }: { item: DailyItem }) {
  const no = item.stage
  const stage = no ? STAGES[Math.max(0, Math.min(TOTAL_STAGES, no) - 1)] : null
  return (
    <div className="dw-stage" title={stage ? `Stage ${no} of ${TOTAL_STAGES}: ${stage.title}` : 'Initial call, before a stage starts'}>
      {stage
        ? <span className="dw-stage-chip"><b>Stage {no}</b></span>
        : <span className="dw-stage-chip is-call"><PhoneCall size={12} aria-hidden="true" />Initial call</span>}
      <small className="dw-stage-name">{stage ? stage.title : 'No order yet'}</small>
      <span className="dw-stage-bar" aria-hidden="true">
        {STAGES.map((s) => <i key={s.no} className={cn(no !== null && s.no < no && 'is-done', s.no === no && 'is-now')} />)}
      </span>
    </div>
  )
}

/** Latest remark plus a compact one-line box to add today's update (the same remark the 48-hour list uses). */
function RemarkCell({ item, canWork, onSave }: { item: DailyItem; canWork: boolean; onSave: (text: string) => Promise<void> }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const value = text.trim()
    if (!value || busy) return
    setBusy(true); setError('')
    try { await onSave(value); setText('') }
    catch (e) { setError(e instanceof Error ? e.message : 'The remark could not be saved.') }
    finally { setBusy(false) }
  }
  return (
    <div className="dw-remark">
      {item.remark && <p className="dw-remark-last" title={`${item.remark.by} · ${new Date(item.remark.at).toLocaleString()}\n${item.remark.body}`}>{item.remark.body}</p>}
      {canWork && <form onSubmit={submit}>
        <Textarea rows={1} value={text} onChange={(e) => setText(e.target.value)} placeholder={item.remark ? 'Add update' : 'Add remark'} maxLength={10000} disabled={busy}
          aria-label={`Remark for ${item.candidate}`} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void submit(e as unknown as FormEvent) } }} />
        <button type="submit" className="dw-remark-save" disabled={busy || !text.trim()} aria-label="Save remark" title="Save remark"><Save size={14} aria-hidden="true" /></button>
      </form>}
      {!item.remark && !canWork && <span className="dw-none">—</span>}
      {error && <small className="dw-remark-error" role="alert">{error}</small>}
    </div>
  )
}

interface MenuEntry { label: string; icon: typeof ArrowRight | typeof ExternalLink; onSelect: () => void }

/** The three-dot menu, drawn on the page body so the scrolling table never clips it. */
function RowMenu({ candidate, items }: { candidate: string; items: MenuEntry[] }) {
  const trigger = useRef<HTMLButtonElement>(null)
  const [open, setOpen] = useState(false)
  const [at, setAt] = useState({ top: 0, left: 0 })
  useEffect(() => {
    if (!open) return
    const close = () => setOpen(false)
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node
      if (!trigger.current?.contains(target) && !(target as HTMLElement).closest?.('.dw-menu')) close()
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { close(); trigger.current?.focus() } }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    window.addEventListener('scroll', close, true)
    window.addEventListener('resize', close)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', close, true)
      window.removeEventListener('resize', close)
    }
  }, [open])
  return (
    <>
      <button ref={trigger} type="button" className="dw-more-btn" aria-label={`More actions for ${candidate}`} aria-haspopup="menu" aria-expanded={open} title="More actions"
        onClick={() => {
          const box = trigger.current?.getBoundingClientRect()
          if (box) {
            const height = items.length * 38 + 12
            setAt({ top: box.bottom + height + 8 > window.innerHeight ? Math.max(8, box.top - height - 4) : box.bottom + 4, left: Math.max(8, box.right - 190) })
          }
          setOpen((value) => !value)
        }}>
        <MoreHorizontal size={16} aria-hidden="true" />
      </button>
      {open && createPortal(
        <div className="dw-menu" role="menu" style={{ top: at.top, left: at.left }}>
          {items.map(({ label, icon: Icon, onSelect }) => (
            <button key={label} type="button" role="menuitem" className="dw-menu-item" onClick={() => { setOpen(false); onSelect() }}><Icon size={15} aria-hidden="true" />{label}</button>
          ))}
        </div>,
        document.body,
      )}
    </>
  )
}
