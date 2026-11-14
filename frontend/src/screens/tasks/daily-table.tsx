import { Fragment, useEffect, useLayoutEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { ArrowRight, CheckCircle2, ExternalLink, Globe, MoreHorizontal, PhoneCall, Save, UserRound } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { countryCode, flagSrc } from '@/shared/lib/country-flag'
import { Button, Textarea } from '@/shared/ui/primitives'
import { RecordIdentity } from '@/shared/ui/workspace'
import { STAGES, TOTAL_STAGES } from '@/shared/workflow/stages'
import { primaryId } from '@/features/candidates/candidate-ids'
import {
  advanceLabel, clientCell, clockTime, countryCell, countryShort, positionCell, remarkBox, rowAction,
  type DailyItem,
} from '@/features/daily-work/daily-work-model'
import { OwnerTag } from './task-identity'

export interface DailyGroup { key: string; title: string; icon: ReactNode; items: DailyItem[]; tone?: 'done' }

/**
 * The agreed column format, in this order for everyone (it mirrors the agency's sheet). The Recruiter column is added after the
 * candidate for supervisors only; the trailing actions column has no visible title (Open / Complete and the row menu live there).
 * Widths are the column plan in px: the table is fixed-layout and each column gets its share of the table width (see
 * columnLayout), so at or above the sum they fill the panel exactly and below it the table scrolls sideways with the actions
 * column kept in view (sticky). The sum (about 1180px with the Recruiter column) fits the panel at a 1500px window with the sidebar
 * open. One alignment per column, the same in header and cell: text columns (client, candidate, recruiter, job, remarks) are
 * left-aligned, the rest (S NO, REF, STAGE, AMOUNT, GOOGLE REVIEW) are centred and the actions are right-aligned. No deadline column.
 */
export const TASK_COLUMNS = [
  { key: 'no', title: 'S NO', width: 40 },
  { key: 'client', title: 'CLIENT / COUNTRY', width: 116, left: true },
  { key: 'candidate', title: 'CANDIDATE NAME', width: 156, left: true },
  { key: 'staff', title: 'Recruiter', width: 94, supervisor: true, left: true },
  { key: 'position', title: 'JOB', width: 112, left: true },
  { key: 'ref', title: 'REF', width: 76 },
  { key: 'stage', title: 'STAGE', width: 156 },
  { key: 'remarks', title: 'REMARKS', width: 148, left: true },
  { key: 'amount', title: 'AMOUNT', width: 80 },
  { key: 'review', title: 'GOOGLE REVIEW', width: 80 },
  { key: 'actions', title: 'Actions', width: 124, quiet: true },
] as const

/** Fixed-layout plan for a column list: the table's minimum width (the sum) and each column's percentage share of the table. */
export function columnLayout(columns: ReadonlyArray<{ width: number }>) {
  const total = columns.reduce((sum, c) => sum + c.width, 0)
  return { minWidth: total, share: (c: { width: number }) => `${((c.width / total) * 100).toFixed(3)}%` }
}

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
  zone?: string
  supervisor: boolean
  staffName: (id: string) => string | undefined
  canWork: (item: DailyItem) => boolean
  onOpen: (item: DailyItem) => void
  onProfile: (item: DailyItem) => void
  onRemark: (item: DailyItem, text: string) => Promise<void>
}

/** The daily list in the reference style: hairline dividers, 72px rows, every cell vertically centred. A "To work" and a "Done today" band only when both exist. */
export function DailyTable({ groups, zone, supervisor, staffName, canWork, onOpen, onProfile, onRemark }: DailyTableProps) {
  const columns = TASK_COLUMNS.filter((c) => supervisor || !('supervisor' in c))
  const layout = columnLayout(columns)
  let serial = 0
  // A single "To work" group needs no band of its own; the bands only tell the two groups apart.
  const bands = groups.length > 1 || groups[0]?.tone === 'done'
  return (
    <ScrollEdges>
      <table className="dw-table dw-main" aria-label="Daily candidates" style={{ minWidth: layout.minWidth }}>
        <colgroup>{columns.map((c) => <col key={c.key} style={{ width: layout.share(c) }} />)}</colgroup>
        <thead><tr>{columns.map((c) => <th key={c.key} scope="col" className={cn('left' in c && 'is-left', c.key === 'actions' && 'is-actions')}>{'quiet' in c ? <span className="sr-only">{c.title}</span> : c.title}</th>)}</tr></thead>
        <tbody>
          {groups.map((g) => <Fragment key={g.key}>
            {bands && <tr className={cn('dw-group', g.tone === 'done' && 'is-done')}>
              <th colSpan={columns.length} scope="colgroup">
                <span className="dw-group-title">{g.icon}{g.title}<b className="tnum">{g.items.length}</b></span>
              </th>
            </tr>}
            {g.items.map((item) => {
              serial += 1
              return <ItemRow key={item.id} item={item} serial={serial} zone={zone} showStaff={supervisor}
                staffName={staffName(item.ownerId)} canWork={canWork(item)} onOpen={() => onOpen(item)} onProfile={() => onProfile(item)}
                onRemark={(text) => onRemark(item, text)} />
            })}
          </Fragment>)}
        </tbody>
      </table>
    </ScrollEdges>
  )
}

function ItemRow({ item, serial, zone, showStaff, staffName, canWork, onOpen, onProfile, onRemark }: {
  item: DailyItem; serial: number; zone?: string; showStaff: boolean; staffName?: string
  canWork: boolean; onOpen: () => void; onProfile: () => void; onRemark: (text: string) => Promise<void>
}) {
  const done = item.status === 'done'
  const action = rowAction(item)
  const noCountry = countryCell(item, countryCode).names.length === 0
  return (
    <tr className={cn('dw-row', done && 'is-done', serial % 2 === 0 && 'is-alt')}>
      <td className="is-num tnum" data-label="S NO">{serial}</td>
      <td data-label="CLIENT / COUNTRY" className={cn('is-left', !item.client && noCountry && 'is-empty')}><ClientCountryCell item={item} /></td>
      <td data-label="CANDIDATE NAME" className="is-left"><RecordIdentity name={item.candidate} detail={<CandidateIds item={item} />} /></td>
      {showStaff && <td data-label="Recruiter" className="is-left">{staffName ? <OwnerTag name={staffName} /> : <span className="dw-none">—</span>}</td>}
      <td data-label="JOB" className={cn('is-left', !item.position && 'is-empty')}><PositionCellView item={item} /></td>
      <td data-label="REF" className={cn(!item.orderRef && 'is-empty')}><OrderRefCell item={item} /></td>
      <td data-label="STAGE"><StageCell item={item} /></td>
      <td data-label="REMARKS" className={cn('is-left', !item.remark && !(canWork && !done) && 'is-empty')}><RemarkCell item={item} canWork={canWork && !done} onSave={onRemark} /></td>
      <td data-label="AMOUNT" className={cn(!advanceLabel(item) && 'is-empty')}><AmountCell item={item} /></td>
      <td data-label="GOOGLE REVIEW" className={cn(!item.googleReview && 'is-empty')}><ReviewCell item={item} /></td>
      <td data-label="Actions" className="is-actions">
        {done && <span className="dw-done-tag"><CheckCircle2 size={13} aria-hidden="true" />Done{item.completedAt ? ` ${clockTime(item.completedAt, zone)}` : ''}</span>}
        <div className="dw-actions">
          <Button size="sm" variant={done ? 'secondary' : 'primary'} className="dw-act" onClick={onOpen} aria-label={`${action === 'Complete' ? 'Complete the stage for' : action} ${item.candidate}`}>
            {action}
          </Button>
          <RowMenu candidate={item.candidate} items={[
            ...(item.applicationId ? [{ label: done ? 'View stage' : 'Open stage', icon: ArrowRight, onSelect: onOpen }] : []),
            { label: 'Open candidate profile', icon: UserRound, onSelect: onProfile },
            ...(noCountry ? [{ label: 'Add preferred country', icon: Globe, onSelect: onProfile }] : []),
          ]} />
        </div>
      </td>
    </tr>
  )
}

/** The ONE ID of the candidate: the Data Bank ID. The CRM reference (CND-...) shows only for an old row that has no Data Bank ID. */
export function CandidateIds({ item }: { item: Pick<DailyItem, 'candidateCareId' | 'candidateRef'> }) {
  const id = primaryId({ careId: item.candidateCareId, ref: item.candidateRef })
  return id ? <b className="cid-main" title={id}>{id}</b> : null
}

/**
 * CLIENT / COUNTRY in one cell: the client's name (up to two lines) and the country chip (flag and short name) under it.
 * A row with only one of them shows just that one; with neither it is a single muted dash. What is missing is added from the row menu.
 */
export function ClientCountryCell({ item }: { item: Pick<DailyItem, 'client' | 'kind' | 'clientContact' | 'country' | 'countrySource' | 'locationHint'> }) {
  const cell = clientCell(item)
  const country = countryCell(item, countryCode)
  if (!cell.text && country.names.length === 0) return <span className="dw-none" title={cell.hint}>—</span>
  return (
    <div className="dw-cc">
      {cell.text && <span className="dw-cell-strong dw-clamp" title={item.clientContact ? `${cell.text}. Contact: ${item.clientContact}` : cell.text}>{cell.text}</span>}
      <CountryChip country={country} />
    </div>
  )
}

/** REF: the order's reference (ORD-...) for the job; a muted dash until the candidate is matched to an order. */
export function OrderRefCell({ item }: { item: Pick<DailyItem, 'orderRef'> }) {
  if (!item.orderRef) return <span className="dw-none" title="Not matched to an order yet">—</span>
  return <span className="dw-ref" title={item.orderRef}>{item.orderRef}</span>
}

/** AMOUNT: the advance the candidate paid (stage 8) as money; a dash while none is recorded. */
export function AmountCell({ item }: { item: Pick<DailyItem, 'advanceAmount' | 'advanceCurrency'> }) {
  const label = advanceLabel(item)
  return label ? <span className="dw-amount tnum" title="Advance collected">{label}</span> : <span className="dw-none" title="No advance recorded yet">—</span>
}

/** GOOGLE REVIEW: Yes once obtained, Pending from stage 9 until it is, a dash before that. */
export function ReviewCell({ item }: { item: Pick<DailyItem, 'googleReview'> }) {
  if (item.googleReview === 'obtained') return <span className="dw-review is-yes">Yes</span>
  if (item.googleReview === 'pending') return <span className="dw-review is-pending" title="Stage 9 reached; Google review not obtained yet">Pending</span>
  return <span className="dw-none">—</span>
}

/** A small neutral chip: the flag (when the app has one) and the short country name. A dashed edge marks a country that is not from an order (the tooltip says where it comes from). */
export function CountryChip({ country }: { country: ReturnType<typeof countryCell> }) {
  const [first, ...more] = country.names
  if (!first) return null
  const code = countryCode(first)
  return (
    <span className={cn('dw-ctry', country.source !== 'order' && 'is-soft')} title={`${country.names.join(', ')}. ${country.hint}`}>
      {code && <img src={flagSrc(code)} alt="" width={16} height={11} loading="lazy" onError={(e) => { e.currentTarget.style.display = 'none' }} />}
      <span>{countryShort(first)}</span>
      {more.length > 0 && <small className="dw-more">+{more.length}</small>}
    </span>
  )
}

/** JOB: up to two lines, the full title in the tooltip; a muted dash when none is known. */
export function PositionCellView({ item }: { item: Pick<DailyItem, 'position' | 'positionSource'> }) {
  const cell = positionCell(item)
  if (!cell.text) return <span className="dw-none" title={cell.hint}>—</span>
  return <span className="dw-cell-strong dw-clamp" title={`${cell.text}. ${cell.hint}`}>{cell.text}</span>
}

/** STAGE: one pill with the stage name (the same width in every row) and a slim progress bar under it. */
export function StageCell({ item }: { item: Pick<DailyItem, 'stage'> }) {
  const no = item.stage
  const stage = no ? STAGES[Math.max(0, Math.min(TOTAL_STAGES, no) - 1)] : null
  return (
    <div className="dw-stage" title={stage ? `Stage ${no} of ${TOTAL_STAGES}: ${stage.title}` : 'Initial call, before a stage starts'}>
      {stage
        ? <span className="dw-stage-chip">Stage {no} · {stage.title}</span>
        : <span className="dw-stage-chip is-call"><PhoneCall size={12} aria-hidden="true" />Initial call</span>}
      <span className="dw-stage-bar" aria-hidden="true">
        {STAGES.map((s) => <i key={s.no} className={cn(no !== null && s.no < no && 'is-done', s.no === no && 'is-now')} />)}
      </span>
    </div>
  )
}

/** Keeps a textarea exactly as tall as its text (one line up to about four, then it scrolls inside); see remarkBox. */
function useAutoGrow(value: string) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    // Empty: always the single idle line (a wrapping placeholder must not make the box taller).
    const box = remarkBox(value ? el.scrollHeight : 0)
    el.style.height = `${box.height}px`
    el.style.overflowY = box.scroll ? 'auto' : 'hidden'
  }, [value])
  return ref
}

/**
 * REMARKS: one muted line (the latest remark, or `lead` such as "Last update 3 days ago" in Follow-up) over a same-width field with
 * the save icon in its bottom-right corner. The field grows with the typed text up to about four lines and shrinks back when text
 * is deleted; the line shows the saved remark wrapped (two lines), the full text in its tooltip. Read-only people see just the line, or a dash.
 */
export function RemarkCell({ item, canWork, onSave, lead }: { item: Pick<DailyItem, 'remark' | 'candidate'>; canWork: boolean; onSave: (text: string) => Promise<void>; lead?: string }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const box = useAutoGrow(text)
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const value = text.trim()
    if (!value || busy) return
    setBusy(true); setError('')
    try { await onSave(value); setText('') }
    catch (e) { setError(e instanceof Error ? e.message : 'The remark could not be saved.') }
    finally { setBusy(false) }
  }
  const last = item.remark
  const line = lead ? (last ? `${lead} · ${last.body}` : lead) : last?.body
  return (
    <div className="dw-remark">
      {line && <p className={cn('dw-remark-last', lead && 'is-lead')} title={`${lead ? `${lead}\n` : ''}${last ? `${last.by} · ${new Date(last.at).toLocaleString()}\n${last.body}` : ''}`.trim()}>{line}</p>}
      {canWork && <form onSubmit={submit}>
        <Textarea ref={box} autoGrow={false} rows={1} value={text} onChange={(e) => setText(e.target.value)} placeholder="Add remark" maxLength={10000} disabled={busy}
          aria-label={`Remark for ${item.candidate}`} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void submit(e as unknown as FormEvent) } }} />
        <button type="submit" className="dw-remark-save" disabled={busy || !text.trim()} aria-label="Save remark" title="Save remark"><Save size={14} aria-hidden="true" /></button>
      </form>}
      {!line && !canWork && <span className="dw-none">—</span>}
      {error && <small className="dw-remark-error" role="alert">{error}</small>}
    </div>
  )
}

export interface MenuEntry { label: string; icon: typeof ArrowRight | typeof ExternalLink; onSelect: () => void }

/** The three-dot menu, drawn on the page body so the scrolling table never clips it. */
export function RowMenu({ candidate, items }: { candidate: string; items: MenuEntry[] }) {
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
