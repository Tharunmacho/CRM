import { Fragment, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { ArrowRight, Check, CheckCircle2, ExternalLink, Globe, MoreHorizontal, PhoneCall, UserRound } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { countryCode } from '@/shared/lib/country-flag'
import { Button, Select } from '@/shared/ui/primitives'
import { RecordIdentity } from '@/shared/ui/workspace'
import { DocInput } from './doc-input'
import { STAGES, TOTAL_STAGES } from '@/shared/workflow/stages'
import { primaryId } from '@/features/candidates/candidate-ids'
import {
  advanceLabel, agencyLabel, clientCell, clientCountry, clockTime, countryCell, docLabel, outcomeDetail, outcomeOf, positionCell, rowAction,
  type DailyItem,
} from '@/features/daily-work/daily-work-model'
import {
  OTHERS_MAX, TYPED_VALUE, canAddTemplate, isAddChoice, isOthersChoice, othersText, remarkSelectView, remarkStatus, textForChoice, type RemarkSaveState,
} from '@/features/daily-work/remark-select-model'
import { createRemarkTemplate, useRemarkTemplates } from '@/features/remark-templates/remark-templates-api'
import { TEMPLATE_MAX, createdText, newTemplateCheck } from '@/features/remark-templates/remark-templates-model'
import { useStore } from '@/shared/state/crm-store'
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
export function ScrollEdges({ children, label = 'Daily candidates, scroll sideways for more columns', labelledBy, busy }: { children: ReactNode; label?: string; /** The id of the filter-bar tab this table answers: the table is then that tab's panel. */ labelledBy?: string; /** The next view is still loading: the rows on screen are the previous ones. */ busy?: boolean }) {
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
    <div className={cn('dw-scroll', edges.left && 'has-left', edges.right && 'has-right', busy && 'is-busy')}>
      <div ref={box} className="tk-table-wrap" tabIndex={0} aria-busy={busy || undefined}
        {...(labelledBy ? { role: 'tabpanel', 'aria-labelledby': labelledBy } : { role: 'region', 'aria-label': label })}>{children}</div>
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
      <table className="dw-table dw-main" aria-label="Daily candidates">
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
      <td data-label="CLIENT" className={cn('is-left', !item.client && 'is-empty')}><ClientCell item={item} /></td>
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
  if (!id) return null
  return <b className="cid-main" title={id}>{id}</b>
}

/**
 * CLIENT: the client's name (up to three lines, the full name and the contact in the tooltip) and, with `country`, the country's name under it in a
 * smaller muted line (plain text, no flag, wrapping at spaces; nothing when no country is known). Without a client it is a single muted dash that
 * says why. What is missing is added from the row menu.
 */
export function ClientCell({ item, country = false }: { item: Pick<DailyItem, 'client' | 'kind' | 'clientContact'> & Partial<Pick<DailyItem, 'country' | 'countrySource' | 'locationHint'>>; country?: boolean }) {
  const cell = clientCell(item)
  const line = country ? clientCountry({ country: item.country ?? null, countrySource: item.countrySource ?? null, locationHint: item.locationHint ?? null }) : { text: '', hint: '' }
  const name = cell.text
    ? <span className="dw-cell-strong dw-clamp" title={item.clientContact ? `${cell.text}. Contact: ${item.clientContact}` : cell.text}>{cell.text}</span>
    : <span className="dw-none" title={cell.hint}>—</span>
  if (!line.text) return name
  return <span className="dw-client">{name}<span className="dw-client-country" title={line.hint}>{line.text}</span></span>
}

/** The recorded agency, or a muted dash when no agency is known. */
export function AgencyCell({ item }: { item: Pick<DailyItem, 'referral' | 'agency'> }) {
  const label = agencyLabel(item)
  if (!label) return <span className="dw-none" title="No agency recorded">—</span>
  return <span className="dw-cell-strong dw-clamp" title={label}>{label}</span>
}

/** REF: the order's reference (ORD-...) for the job; a muted dash until the candidate is matched to an order. */
export function OrderRefCell({ item }: { item: Pick<DailyItem, 'orderRef'> }) {
  if (!item.orderRef) return <span className="dw-none" title="Not matched to an order yet">—</span>
  return <span className="dw-ref" title={item.orderRef}>{item.orderRef}</span>
}

/** AMOUNT: the advance the candidate paid (stage 9) as money; a dash while none is recorded. */
export function AmountCell({ item }: { item: Pick<DailyItem, 'advanceAmount' | 'advanceCurrency'> }) {
  const label = advanceLabel(item)
  return label ? <span className="dw-amount tnum" title="Advance collected">{label}</span> : <span className="dw-none" title="No advance recorded yet">—</span>
}

/** GOOGLE REVIEW: Yes once obtained, Pending from stage 10 until it is, a dash before that. */
export function ReviewCell({ item }: { item: Pick<DailyItem, 'googleReview'> }) {
  if (item.googleReview === 'obtained') return <span className="dw-review is-yes">Yes</span>
  if (item.googleReview === 'pending') return <span className="dw-review is-pending" title="Stage 10 reached; Google review not obtained yet">Pending</span>
  return <span className="dw-none">—</span>
}

/** JOB: up to two lines, the full title in the tooltip; a muted dash when none is known. */
export function PositionCellView({ item }: { item: Pick<DailyItem, 'position' | 'positionSource'> }) {
  const cell = positionCell(item)
  if (!cell.text) return <span className="dw-none" title={cell.hint}>—</span>
  return <span className="dw-cell-strong dw-clamp" title={`${cell.text}. ${cell.hint}`}>{cell.text}</span>
}

/**
 * STAGE of a placement that ended: the outcome chip (Declined, Rejected or Travelled) with where it ended ("at 5 · Offer letter") inside it, and under it,
 * in plain muted lines nobody has to hover for: WHY (the reason), WHEN ("19 Nov 2026") and, when the server names them, "by <person>". It replaces the
 * progress dashes. Everything wraps; nothing is cut.
 */
export function OutcomeStage({ item, zone }: { item: Pick<DailyItem, 'stage' | 'stageTitle'> & { outcome?: DailyItem['outcome'] }; zone?: string }) {
  const outcome = outcomeOf(item)
  const stage = outcome.stage ?? item.stage ?? null
  const titleOf = (no: number) => (no === item.stage && item.stageTitle) || STAGES[no - 1]?.title
  const detail = outcomeDetail({ ...outcome, stage }, titleOf, zone)
  const meta = [detail.when, detail.by].filter(Boolean).join(' · ')
  const tip = [outcome.label, detail.where, detail.why, meta].filter(Boolean).join(' · ')
  return (
    <div className={cn('dw-stage dw-outcome', `is-${outcome.kind}`)} title={tip}>
      <span className={cn('dw-outcome-chip', `is-${outcome.kind}`)}>
        <b>{outcome.label}</b>
        {detail.where && <span className="dw-outcome-at">{detail.where}</span>}
      </span>
      {detail.why && <span className="dw-outcome-why">{detail.why}</span>}
      {meta && <span className="dw-outcome-meta">{meta}</span>}
    </div>
  )
}

/** STAGE: one pill with the stage name (the same width in every row) and a slim progress bar under it; a placement that ended shows its outcome instead. */
export function StageCell({ item, zone }: { item: Pick<DailyItem, 'stage'> & { outcome?: DailyItem['outcome']; stageTitle?: string | null }; zone?: string }) {
  if (outcomeOf(item).kind !== 'current') return <OutcomeStage item={item} zone={zone} />
  const no = item.stage
  const stage = no ? STAGES[Math.max(0, Math.min(TOTAL_STAGES, no) - 1)] : null
  return (
    <div className="dw-stage" title={stage ? `Stage ${no} of ${TOTAL_STAGES}: ${stage.title}` : 'Initial call, before a stage starts'}>
      {stage
        ? <span className="dw-stage-chip"><span className="dw-stage-text"><b>{no}</b> · {stage.title}</span></span>
        : <span className="dw-stage-chip is-call"><PhoneCall size={12} aria-hidden="true" /><span className="dw-stage-text">Initial call</span></span>}
      <span className="dw-stage-bar" aria-hidden="true">
        {STAGES.map((s) => <i key={s.no} className={cn(no !== null && s.no < no && 'is-done', s.no === no && 'is-now')} />)}
      </span>
    </div>
  )
}

/**
 * REMARKS: a compact dropdown. It shows the candidate's current remark as its value ("Select remark" while there is none) and offers the
 * active remark templates, then "Others"; choosing a template saves it at once through `onSave`. "Others" turns the cell into a one-line
 * box of the same height (autofocus, Enter or the check button saves through the same call, Esc or an empty box cancels), and the saved
 * text then shows as the dropdown's value: that is the only way to type. Admins also get "+ Add a template" under it, which opens the same
 * kind of box and adds the text as a template. A remark that is not a template is simply shown as the value (it is never labelled custom).
 * The status (Saving, Saved or what went wrong) is a badge on the control's edge in the Tasks table (src/styles/tasks-table.css) and a line
 * only in Follow-up, and it is always in a polite live region, so a row never changes height when it says something. Read-only people see
 * the remark as plain text, or a dash. `lead` is a muted line such as "Last update 3 days ago" shown while nothing else needs saying.
 */
export function RemarkCell({ item, canWork, onSave, lead }: { item: Pick<DailyItem, 'remark' | 'candidate'>; canWork: boolean; onSave: (text: string) => Promise<void>; lead?: string }) {
  const templates = useRemarkTemplates()
  const canAdd = canAddTemplate(useStore().me.role)
  const [mode, setMode] = useState<'select' | 'others' | 'template'>('select')
  const [typed, setTyped] = useState('')
  const box = useRef<HTMLDivElement>(null)
  const [state, setState] = useState<RemarkSaveState>('idle')
  const [error, setError] = useState('')
  const [pending, setPending] = useState<string | null>(null)
  const [pendingText, setPendingText] = useState('')
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const last = item.remark
  const view = remarkSelectView(last?.body, templates.items, templates.loaded, canAdd)
  const tip = last ? `${last.by} · ${new Date(last.at).toLocaleString()}\n${last.body}` : ''

  if (!canWork) {
    const line = lead ? (last ? `${lead} · ${last.body}` : lead) : last?.body
    return (
      <div className="dw-remark">
        {line ? <p className={cn('dw-remark-last', lead && 'is-lead')} title={`${lead ? `${lead}\n` : ''}${tip}`.trim()}>{line}</p> : <span className="dw-none">—</span>}
      </div>
    )
  }

  /** Save `text` as this candidate's remark: the one path a chosen template, a typed remark and a newly added template all take. Says whether it worked. */
  const save = async (text: string, value: string | null): Promise<boolean> => {
    window.clearTimeout(timer.current)
    setPending(value); setPendingText(text); setState('saving'); setError('')
    try {
      await onSave(text)
      setState('saved')
      timer.current = window.setTimeout(() => setState('idle'), 2500)
      return true
    } catch (failure) {
      setState('error'); setError(failure instanceof Error ? failure.message : 'The remark could not be saved.')
      return false
    } finally { setPending(null) }
  }
  const choose = async (value: string) => {
    if (state === 'saving') return
    if (isOthersChoice(value)) { setTyped(''); setError(''); setState('idle'); setMode('others'); return }
    if (isAddChoice(value)) { if (canAdd) { setTyped(''); setError(''); setState('idle'); setMode('template') } return }
    const text = textForChoice(value, templates.items)
    if (text) await save(text, value)
  }
  const stopTyping = () => {
    setMode('select'); setTyped(''); setError(''); setState('idle')
    window.setTimeout(() => box.current?.querySelector<HTMLElement>('.custom-select-trigger')?.focus(), 0)
  }
  /** Enter or the check button: the typed remark is saved as it is; admins' "+ Add a template" adds the typed text as a template (the server answers with the new or the already existing one) and selects it. An empty box cancels. */
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (state === 'saving') return
    if (mode === 'others') {
      const text = othersText(typed)
      if (!text) { stopTyping(); return }
      setMode('select')
      if (!(await save(text, TYPED_VALUE))) setMode('others')
      else setTyped('')
      return
    }
    if (!canAdd) return
    const check = newTemplateCheck(typed, templates.items)
    if (!check.ok) { setState('error'); setError(check.message); return }
    setState('saving'); setError('')
    let text: string
    try { text = createdText(await createRemarkTemplate(check.text), check.text) }
    catch (failure) { setState('error'); setError(failure instanceof Error ? failure.message : 'The template could not be added.'); return }
    setMode('select'); setTyped('')
    await save(text, null)
  }
  const status = remarkStatus(state, error, lead)
  const typing = mode !== 'select'
  const options = pending === TYPED_VALUE ? [...view.options, { value: TYPED_VALUE, label: pendingText }] : view.options
  return (
    <div className="dw-remark" ref={box} title={tip || undefined}>
      {typing ? (
        <form className="dw-remark-add" onSubmit={(event) => void submit(event)}>
          <input type="text" autoFocus autoComplete="off" maxLength={mode === 'others' ? OTHERS_MAX : TEMPLATE_MAX} value={typed} disabled={state === 'saving'}
            placeholder={mode === 'others' ? 'Type a remark, then Enter' : 'New template, then Enter'}
            aria-label={mode === 'others' ? `Remark for ${item.candidate}` : `New remark template for ${item.candidate}`} aria-invalid={state === 'error' ? true : undefined}
            onChange={(event) => { setTyped(event.target.value); if (state === 'error') { setState('idle'); setError('') } }}
            onKeyDown={(event) => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); stopTyping() } }}
            onBlur={(event) => { if (!typed.trim() && state !== 'saving' && !event.relatedTarget) stopTyping() }} />
          <button type="submit" className="dw-remark-ok" aria-label={mode === 'others' ? 'Save the remark' : 'Add the template'} title="Save (Enter)" disabled={state === 'saving' || !typed.trim()}>
            <Check size={15} aria-hidden="true" />
          </button>
        </form>
      ) : <Select compact searchable={false} className="dw-remark-select" aria-label={`Remark for ${item.candidate}`}
        value={pending ?? view.value} disabled={state === 'saving'} onChange={(event) => void choose(event.target.value)}>
        {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </Select>}
      <small className={cn('dw-remark-status', `is-${status.tone}`)} title={status.tone === 'error' ? status.text : undefined} role="status" aria-live="polite">{status.text}</small>
    </div>
  )
}

/** DOC: the date of commencement, typed right in the cell as dd/mm/yyyy (saved on Enter or when the box is left). A dash when there is no placement yet or the person cannot edit; read-only people see the date as "19 Nov 2026". */
export function DocCell({ item, canAdd, onSave }: { item: Pick<DailyItem, 'commencementDate' | 'candidate' | 'applicationId'>; canAdd: boolean; onSave: (iso: string | null) => Promise<void> }) {
  const label = docLabel(item.commencementDate)
  if (canAdd && item.applicationId) return <DocInput value={item.commencementDate ?? null} candidate={item.candidate} onSave={onSave} />
  if (label) return <time className="dw-doc tnum" dateTime={item.commencementDate ?? undefined} title="Date of commencement">{label}</time>
  return <span className="dw-none" title={item.applicationId ? 'No date of commencement yet' : 'Choose a job first, then add the date of commencement'}>—</span>
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
