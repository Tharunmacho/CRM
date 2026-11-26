import { useEffect, useRef, useState, type ReactNode } from 'react'
import { AlertCircle, Check, ExternalLink, FileText, Loader2, Plus, RefreshCw, Trash2, Upload } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { AutoTextarea, Button, Select } from '@/shared/ui/primitives'
import { DatePicker } from '@/shared/ui/date-picker'
import { Dialog } from '@/shared/ui/overlay'
import { SuggestInput, useSuggestions } from '@/shared/ui/suggest-input'
import { fileHref } from '@/shared/api/client'
import {
  adFileError, adKind, adSrc, closedReasonLabel, closedTitle, deadlineChip, fieldDisplay, flipStatus, hasDetails, isoToText, positionsInfo, positionsSummary, readFieldInput, readTypedDate, shapeDateText, statusLabel,
  type RequirementField, type RequirementItem,
} from '@/features/job-requirements/job-requirements-model'
import type { RowSave } from '@/features/job-requirements/use-job-requirements'

type Read = (text: string) => { ok: true; store: string } | { ok: false; error: string }

interface InlineCellProps {
  /** The saved value in the form the server keeps it (a date is YYYY-MM-DD). */
  saved: string
  /** How a saved value is written in the box. */
  show: (saved: string) => string
  /** Check what was typed; `store` is what is sent. */
  read: Read
  /** Shapes the text while typing (a date gets its slashes). */
  shape?: (raw: string) => string
  onSave: (store: string) => void
  label: string
  placeholder?: string
  inputRef?: (node: HTMLInputElement | HTMLTextAreaElement | null) => void
  /** Suggestions (a country list): free text is still allowed. */
  options?: readonly string[]
  inputMode?: 'text' | 'decimal' | 'numeric'
  maxLength?: number
  /** A small calendar button beside the box (dates). */
  picker?: boolean
  /** A box that wraps a long text over several lines (Enter still saves). */
  multiline?: boolean
  /** Centre the wrapped text (Country) instead of left-aligning it (Client name). */
  centered?: boolean
}

/**
 * One typed cell of the sheet. What is typed is kept in the box whatever happens: it is saved when the box is left or Enter is pressed, a wrong
 * value (a half-typed date, a word in a number column) is flagged and kept for correcting, Escape puts the saved value back. While the box has
 * the focus a re-read of the list never overwrites it.
 */
export function InlineCell({ saved, show, read, shape, onSave, label, placeholder, inputRef, options, inputMode, maxLength, picker, multiline, centered }: InlineCellProps) {
  const shown = show(saved)
  const [text, setText] = useState(shown)
  const [error, setError] = useState('')
  const focused = useRef(false)
  useEffect(() => { if (!focused.current) { setText(shown); setError('') } }, [shown])

  const commit = () => {
    const result = read(text)
    if (!result.ok) { setError(result.error); return }
    setError('')
    if (result.store === saved) { setText(show(result.store)); return }
    onSave(result.store)
  }
  const change = (raw: string) => { setText(shape ? shape(raw) : raw); if (error) setError('') }
  const common = {
    value: text, placeholder, maxLength, inputMode, 'aria-label': label, 'aria-invalid': error ? true : undefined, title: error || undefined,
    className: cn('jr-input', error && 'is-error'),
    onFocus: () => { focused.current = true },
    onBlur: () => { focused.current = false; commit() },
    onKeyDown: (event: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      // A suggestion picked with Enter has already been handled (and the event cancelled): the box keeps the focus until it is left.
      if (event.defaultPrevented) return
      if (event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.blur() }
      else if (event.key === 'Escape') { setText(shown); setError(''); event.currentTarget.blur() }
    },
  }
  return (
    <div className={cn('jr-cell-box', picker && 'has-picker')}>
      {multiline && options
        ? <SuggestArea common={common} options={options} inputRef={inputRef} change={change} className={cn('jr-input is-multiline', centered && 'is-centered', error && 'is-error')} />
        : multiline
        ? <AutoTextarea {...(common as object)} ref={inputRef} rows={1} minRows={1} maxRows={6} className={cn('jr-input is-multiline', centered && 'is-centered', error && 'is-error')} onChange={(event) => change(event.target.value)} />
        : options
        ? <SuggestInput {...common} ref={inputRef} options={options} onChange={change} />
        : <input {...common} ref={inputRef} type="text" autoComplete="off" onChange={(event) => change(event.target.value)} />}
      {picker && (
        <DatePicker compact className="jr-datepick" aria-label={`${label}: pick from the calendar`} value={saved}
          onChange={(iso) => { setText(isoToText(iso)); setError(''); if (iso !== saved) onSave(iso) }} />
      )}
      {error && <span className="jr-cell-error" role="alert">{error}</span>}
    </div>
  )
}

/** A wrapping text box (several lines) with our own suggestion list, like SuggestInput: free text is still allowed. */
function SuggestArea({ common, options, inputRef, change, className }: {
  common: Record<string, unknown> & { value: string; onFocus: () => void; onBlur: () => void; onKeyDown: (event: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => void }
  options: readonly string[]
  inputRef?: (node: HTMLInputElement | HTMLTextAreaElement | null) => void
  change: (value: string) => void
  className: string
}) {
  const wrapper = useRef<HTMLSpanElement>(null)
  const { popup, setOpen, inputProps } = useSuggestions({ anchor: wrapper, options, query: common.value, onPick: change })
  return (
    <span ref={wrapper} className="relative block w-full">
      <AutoTextarea
        {...(common as object)} {...inputProps} ref={inputRef} rows={1} minRows={1} maxRows={4} className={className}
        onChange={(event) => { change(event.target.value); setOpen(true) }}
        onFocus={() => { setOpen(true); common.onFocus() }}
        onClick={() => setOpen(true)}
        onBlur={() => { setOpen(false); common.onBlur() }}
        onKeyDown={(event) => { inputProps.onKeyDown(event); common.onKeyDown(event) }}
      />
      {popup}
    </span>
  )
}

export const textRead: Read = (text) => ({ ok: true, store: text.replace(/\s+/g, ' ').trim() })
export const identity = (value: string) => value
export const dateRead: Read = (text) => { const edit = readTypedDate(text); return edit.ok ? { ok: true, store: edit.iso ?? '' } : edit }

/** A column the team added: typed by the field's type. */
export function CustomCell({ field, item, onSave, label }: { field: RequirementField; item: RequirementItem; onSave: (value: string) => void; label: string }) {
  const stored = item.custom?.[field.id] ?? ''
  if (field.type === 'yes_no') {
    return (
      <Select compact className="jr-yesno" aria-label={label} value={stored} onChange={(event) => { if (event.target.value !== stored) onSave(event.target.value) }}>
        <option value="">-</option>
        <option value="yes">Yes</option>
        <option value="no">No</option>
      </Select>
    )
  }
  const date = field.type === 'date'
  return (
    <InlineCell
      saved={stored} label={label} picker={date}
      show={(value) => fieldDisplay(field, value)}
      read={(text) => { const edit = readFieldInput(field, text); return edit.ok ? { ok: true, store: edit.value } : edit }}
      shape={date ? shapeDateText : undefined}
      placeholder={date ? 'dd/mm/yyyy' : undefined}
      inputMode={field.type === 'number' ? 'decimal' : date ? 'numeric' : 'text'}
      maxLength={date ? 10 : 500}
      onSave={onSave}
    />
  )
}

/**
 * Status as a real toggle switch: on (green) = Active, off (grey) = Closed, the word beside it. Space and Enter flip it (it is a button with role=switch); turning it off
 * asks why first (the screen opens the reason dialog), turning it on reopens. A closed row shows its reason under the switch; the closed date is the tooltip.
 */
export function StatusToggle({ item, onFlip }: { item: RequirementItem; onFlip: () => void }) {
  const next = statusLabel(flipStatus(item.status))
  const reason = item.status === 'closed' ? closedReasonLabel(item.closedReason) : ''
  return (
    <span className="jr-status-box">
      <button type="button" role="switch" aria-checked={item.status === 'active'} className={cn('jr-switch', item.status === 'active' ? 'is-on' : 'is-off')} onClick={onFlip}
        aria-label={`Active: ${item.clientName || 'this requirement'}${reason ? `, closed because ${reason}` : ''}`}
        title={item.status === 'closed' ? `${closedTitle(item)}. Switch on to reopen` : `Switch off to make it ${next}`}>
        <span className="jr-switch-track" aria-hidden="true"><span className="jr-switch-thumb" /></span>
        <span className="jr-switch-label">{statusLabel(item.status)}</span>
      </button>
      {reason && <span className="jr-status-reason" title={closedTitle(item)}>{reason}</span>}
    </span>
  )
}

/**
 * The Job positions cell: the job titles as small chips with the number of positions and the vacancies total, or (an old row with only flat details) their one-line
 * summary; "Click to open the form" when empty. It opens the form; an active requirement with a deadline carries a chip (amber within 7 days, red once overdue).
 */
export function DetailsButton({ item, today, onOpen }: { item: RequirementItem; today: string; onOpen: () => void }) {
  const info = positionsInfo(item)
  const summary = positionsSummary(item)
  const chip = deadlineChip(item, today)
  // A row from before the multi-position form only has the flat keys: show its one position the same way.
  const old = info.count === 0 ? item.details : null
  const oldTitle = (old?.position ?? '').trim()
  const titles = info.count > 0 ? info.titles : oldTitle ? [oldTitle] : []
  const shown = titles.slice(0, 3)
  const more = titles.length - shown.length
  const facts = info.count > 0
    ? [info.count > 1 ? `${info.count} positions` : '', info.vacancies !== null ? `${info.vacancies} ${info.vacancies === 1 ? 'vacancy' : 'vacancies'}` : ''].filter(Boolean).join(' · ')
    : [old?.vacancies, old?.placeOfWork, old?.basicSalary].map((value) => (value ?? '').trim()).filter(Boolean).join(' · ')
  const filled = hasDetails(item)
  return (
    <span className="jr-details-box">
      <button type="button" className={cn('jr-details', filled && 'is-filled')} onClick={onOpen} aria-label={`Job positions of ${item.clientName || 'this requirement'}: ${summary || 'empty'}. Open the form`}
        title={summary ? `${summary} (open the form)` : 'Open the requirement form'}>
        {filled
          ? <span className="jr-details-text">
            {shown.length > 0
              ? <span className="jr-pos-chips">{shown.map((title, index) => <span key={`${title}-${index}`} className="jr-pos-chip" title={title}>{title}</span>)}{more > 0 && <span className="jr-pos-chip is-more">+{more}</span>}</span>
              : <span className="jr-pos-facts">{summary || 'Details added'}</span>}
            <span className="jr-pos-facts" title={facts || undefined}>{facts || 'Open the form'}</span>
          </span>
          : <><Plus size={14} aria-hidden="true" /><span>Add positions</span></>}
      </button>
      {chip && <span className={cn('jr-chip', `is-${chip.tone}`)} title={`Deadline ${isoToText(item.details.deadline)}`}>{chip.text}</span>}
    </span>
  )
}

/** The little status of a row's save: Saving..., Saved, or "Not saved" with the reason and a Retry. */
export function SaveMark({ save, onRetry }: { save: RowSave | undefined; onRetry: () => void }) {
  if (!save) return null
  if (save.state === 'saving') return <span className="jr-save is-saving" role="status"><Loader2 size={12} className="jr-spin" aria-hidden="true" />Saving</span>
  if (save.state === 'saved') return <span className="jr-save is-saved" role="status"><Check size={12} aria-hidden="true" />Saved</span>
  return (
    <span className="jr-save is-error" role="alert" title={save.message}>
      <AlertCircle size={12} aria-hidden="true" />Not saved
      <button type="button" className="jr-retry" onClick={onRetry}>Retry</button>
    </span>
  )
}

/** The Ad: a thumbnail (a PDF is a link), or "Upload photo" when empty, with Replace and Remove. */
export function AdCell({ item, onUpload, onRemove, onError }: { item: RequirementItem; onUpload: (file: File) => Promise<void>; onRemove: () => Promise<void>; onError: (message: string) => void }) {
  const file = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [view, setView] = useState(false)
  const kind = adKind(item)
  const name = item.clientName || 'this requirement'
  const choose = async (picked: File | undefined) => {
    if (file.current) file.current.value = ''
    if (!picked) return
    const problem = adFileError(picked)
    if (problem) { onError(problem); return }
    setBusy(true)
    try { await onUpload(picked) } catch (failure) { onError(failure instanceof Error ? failure.message : 'The file could not be uploaded.') } finally { setBusy(false) }
  }
  const remove = async () => {
    setBusy(true)
    try { await onRemove() } catch (failure) { onError(failure instanceof Error ? failure.message : 'The file could not be removed.') } finally { setBusy(false) }
  }
  const picker = <input ref={file} type="file" accept="image/*,application/pdf,.pdf" className="jr-file" tabIndex={-1} aria-hidden="true" onChange={(event) => void choose(event.target.files?.[0])} />
  if (busy) return <span className="jr-ad-busy" role="status"><Loader2 size={14} className="jr-spin" aria-hidden="true" />Working</span>
  if (kind === 'none') {
    return (
      <>
        {picker}
        <button type="button" className="jr-upload" onClick={() => file.current?.click()} aria-label={`Upload the ad photo for ${name}`} title="Upload photo (an image or a PDF, up to 10 MB)"><Upload size={15} aria-hidden="true" /><span>Upload</span></button>
      </>
    )
  }
  return (
    <div className="jr-ad">
      {picker}
      {kind === 'image'
        ? <button type="button" className="jr-thumb" onClick={() => setView(true)} aria-label={`Open the ad of ${name}`} title={item.adName ?? 'Open the ad'}><img src={fileHref(adSrc(item))} alt="" loading="lazy" decoding="async" /></button>
        : <a className="jr-thumb is-pdf" href={fileHref(item.adUrl ?? '')} target="_blank" rel="noreferrer" aria-label={`Open the PDF ad of ${name}`} title={item.adName ?? 'Open the PDF'}><FileText size={20} aria-hidden="true" /><span>PDF</span></a>}
      <span className="jr-ad-actions">
        <button type="button" className="jr-mini" onClick={() => file.current?.click()} aria-label={`Replace the ad of ${name}`} title="Replace"><RefreshCw size={13} aria-hidden="true" /></button>
        <button type="button" className="jr-mini is-danger" onClick={() => void remove()} aria-label={`Remove the ad of ${name}`} title="Remove"><Trash2 size={13} aria-hidden="true" /></button>
      </span>
      {view && (
        <Dialog open onClose={() => setView(false)} title={`Ad: ${item.clientName || 'requirement'}`} description={item.adName ?? undefined} width="lg"
          footer={<><Button variant="secondary" onClick={() => file.current?.click()} icon={<RefreshCw size={15} />}>Replace</Button>{' '}<Button variant="primary" onClick={() => setView(false)}>Close</Button></>}>
          <img className="jr-lightbox" src={fileHref(adSrc(item))} alt={`Ad of ${item.clientName || 'the requirement'}`} />
          <a className="jr-lightbox-link" href={fileHref(item.adUrl ?? '')} target="_blank" rel="noreferrer"><ExternalLink size={14} aria-hidden="true" />Open in a new tab</a>
        </Dialog>
      )}
    </div>
  )
}

/** The header of an added column: its name and a small menu content is passed in as `menu`. */
export function ColumnHead({ field, menu }: { field: RequirementField; menu: ReactNode }) {
  return <span className="jr-colhead"><span className="jr-colhead-name" title={field.label}>{field.label}</span>{menu}</span>
}
