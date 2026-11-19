import { useRef, useState } from 'react'
import { AlertTriangle, Check, CloudUpload, FileText, Image as ImageIcon, LoaderCircle, Plus, RotateCw, X } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { DOCUMENT_ACCEPT, DOCUMENT_HELP } from '@/shared/lib/document-files'
import { FileLink } from '@/shared/ui/file-drop'
import { chipLabel, findingsNames, PLACE_BUTTONS, PLACE_CHOICES, sizeText, type FileRow, type FoundChip, type ReadProgress } from '../entry-upload'
import type { JobReport } from '../read-report'
import { LogToggle, ReadNotes } from './read-report'

const isImage = (name: string) => /\.(jpe?g|png|webp|heic|heif|tiff?)$/i.test(name)

/**
 * The one place documents are added on Add candidate. Any file may hold any mix of documents, so there is no slot per kind: files go in
 * as they are and the screen reads them by itself. Each row shows how far its reading is, and a chip appears for every kind the server
 * finds (resume, passport, Aadhaar, education and experience certificates, medical, signed offer, visa, police clearance, photo, other)
 * with its page count. Resume, passport and Aadhaar are read into the form; the rest are filed in their own field when the candidate is saved.
 */
export function EntryUploadZone({ rows, chips, reports = [], progress, disabled, max, problems, onAdd, onRemove, onRetry, onCheckAgain, onManual, onPlacePage }: {
  rows: FileRow[]
  chips: FoundChip[]
  /** What the server says it read, per file (shown only for files that came back with a coverage record). */
  reports?: JobReport[]
  progress: ReadProgress
  disabled: boolean
  max: number
  problems: string[]
  onAdd: (files: File[]) => void
  onRemove: (id: string) => Promise<void> | void
  onRetry: (id: string) => Promise<void> | void
  /** Ask the server again whether the document reader is running (shown on a file that is not moving). */
  onCheckAgain?: () => Promise<void> | void
  /** "Enter details manually" on a file that could not be read: take the person to the form. */
  onManual?: () => void
  /** A page the sorting could not place: the person's choice of the field it belongs in (the server files it). */
  onPlacePage?: (id: string, page: number, kind: string) => Promise<void> | void
}) {
  const input = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [removing, setRemoving] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)
  const [logOpen, setLogOpen] = useState<string | null>(null)
  const [placing, setPlacing] = useState<string | null>(null)
  // Only what was found has a chip (one per kind, with its pages); a kind that is not in the files is not listed.
  const found = chips.filter((chip) => chip.state !== 'idle')
  const full = rows.length >= max
  const locked = disabled || full

  const take = (list: FileList | null) => {
    if (locked || !list || list.length === 0) return
    onAdd([...list])
  }
  const remove = async (id: string) => {
    setRemoving(id)
    try { await onRemove(id) } finally { setRemoving(null) }
  }

  const place = async (id: string, page: number, kind: string) => {
    setPlacing(`${id}:${page}`)
    try { await onPlacePage?.(id, page, kind) } finally { setPlacing(null) }
  }

  const checkAgain = async () => {
    setChecking(true)
    try { await onCheckAgain?.() } finally { setChecking(false) }
  }

  return (
    <div
      id="upload-zone"
      className={cn('uz', dragging && 'is-dragging', locked && 'is-locked')}
      onDragOver={(event) => { event.preventDefault(); if (!locked) setDragging(true) }}
      onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false) }}
      onDrop={(event) => { event.preventDefault(); setDragging(false); take(event.dataTransfer.files) }}
    >
      <div className={cn('uz-drop', rows.length > 0 && 'is-compact')} onClick={(event) => { if (!(event.target as HTMLElement).closest('button')) input.current?.click() }}>
        <span className="uz-drop-icon" aria-hidden><CloudUpload size={rows.length ? 18 : 24} /></span>
        <div className="uz-drop-copy">
          <strong>{dragging ? 'Drop to add' : rows.length ? 'Add more files' : 'Drop files here'}</strong>
          <span>{full ? `You have reached the limit of ${max} files.` : DOCUMENT_HELP}</span>
        </div>
        <button type="button" className="uz-choose" disabled={locked} onClick={() => input.current?.click()}>
          <Plus size={16} aria-hidden />Choose files
        </button>
        <input ref={input} type="file" multiple accept={DOCUMENT_ACCEPT} aria-label="Choose documents" className="sr-only" tabIndex={-1} disabled={locked}
          onChange={(event) => { const list = event.currentTarget.files; take(list); event.currentTarget.value = '' }} />
      </div>

      {rows.length === 0 && <p className="uz-hint">One file or many. We sort every page for you: resume, passport, Aadhaar, education and experience certificates, medical, signed offer, visa, police clearance and photo.</p>}

      {problems.length > 0 && <ul role="alert" className="uz-problems">{problems.map((message) => <li key={message}>{message}</li>)}</ul>}

      {rows.length > 1 && (
        <div className="uz-progress" aria-live="polite">
          <span className="uz-progress-bar" role="progressbar" aria-label="Files read" aria-valuemin={0} aria-valuemax={progress.total} aria-valuenow={progress.finished}><i style={{ width: `${progress.percent}%` }} /></span>
          <span className="tnum">{progress.text}{progress.failed > 0 ? `, ${progress.failed} failed` : ''}</span>
        </div>
      )}

      {rows.length > 0 && (
        <ul className={cn('uz-list', rows.length > 6 && 'is-long')} aria-label="Files added">
          {rows.map((row) => {
            const Icon = isImage(row.name) ? ImageIcon : FileText
            const found = findingsNames(row.findings)
            const notes = reports.find((item) => item.id === row.id)?.report.notes ?? []
            const lines = [row.note, row.hint].filter(Boolean)
            const logCount = lines.length + notes.length
            const open = logOpen === row.id
            const logId = `uz-log-${row.id}`
            return (
              <li key={row.id} className={cn('uz-row', `is-${row.state}`, row.stalled && 'is-stalled')}>
                <div className="uz-line">
                  <span className="uz-ico" aria-hidden><Icon size={18} /></span>
                  <div className="uz-main">
                    <p className="uz-name">{row.url ? <FileLink url={row.url} className="hover:text-navy-700">{row.name}</FileLink> : row.name}</p>
                    <p className="uz-meta tnum">{sizeText(row.sizeKb)}{found && <span className="uz-found"> · {found}</span>}</p>
                  </div>
                  <div className="uz-tools">
                    <span className={`ac-pill is-${row.tone}`} role="status"><i aria-hidden />{row.label}</span>
                    {logCount > 0 && <LogToggle open={open} count={logCount} controls={logId} onToggle={() => setLogOpen(open ? null : row.id)} />}
                    {row.canRetry && !row.retryLabel && <button type="button" className="uz-icon-btn" disabled={disabled} onClick={() => void onRetry(row.id)} aria-label={`Read ${row.name} again`} title="Read again"><RotateCw size={16} aria-hidden /></button>}
                    <button type="button" className="uz-icon-btn is-remove" disabled={disabled || removing === row.id} onClick={() => void remove(row.id)} aria-label={`Remove ${row.name}`} title="Remove">
                      {removing === row.id ? <LoaderCircle size={16} className="ac-spin" aria-hidden /> : <X size={16} aria-hidden />}
                    </button>
                  </div>
                </div>
                {row.sortLine && <p className="uz-sort tnum" role="status"><LoaderCircle size={13} className="ac-spin" aria-hidden /> {row.sortLine}</p>}
                {row.foundLines.length > 0 && <ul className="uz-sorted" aria-label="Pages found">{row.foundLines.map((line) => <li key={line}><Check size={13} strokeWidth={3} aria-hidden /> {line}</li>)}</ul>}
                {row.unplaced.length > 0 && onPlacePage && (
                  <ul className="uz-unplaced" aria-label="Pages that could not be placed">
                    {row.unplaced.map((item) => {
                      const key = `${row.id}:${item.page}`
                      return (
                        <li key={key}>
                          <span><AlertTriangle size={13} aria-hidden /> Page {item.page}: {item.reason}. Which field is it for?</span>
                          <span className="uz-place">
                            {PLACE_CHOICES.slice(0, PLACE_BUTTONS).map((kind) => (
                              <button key={kind} type="button" className="uz-check" disabled={disabled || placing === key} onClick={() => void place(row.id, item.page, kind)}>{chipLabel(kind)}</button>
                            ))}
                            <select aria-label={`Another field for page ${item.page}`} className="uz-check" value="" disabled={disabled || placing === key} onChange={(event) => { if (event.target.value) void place(row.id, item.page, event.target.value) }}>
                              <option value="">More…</option>
                              {PLACE_CHOICES.slice(PLACE_BUTTONS).map((kind) => <option key={kind} value={kind}>{chipLabel(kind)}</option>)}
                            </select>
                          </span>
                        </li>
                      )
                    })}
                  </ul>
                )}
                {(row.canCheckAgain && onCheckAgain || row.retryLabel || row.canEnterManually && onManual) && (
                  <div className="uz-actions">
                    {row.canCheckAgain && onCheckAgain && (
                      <button type="button" className="uz-check" disabled={checking} onClick={() => void checkAgain()}>
                        {checking ? <LoaderCircle size={14} className="ac-spin" aria-hidden /> : <RotateCw size={14} aria-hidden />}Check again
                      </button>
                    )}
                    {row.retryLabel && (
                      <button type="button" className="uz-check" disabled={disabled} onClick={() => void onRetry(row.id)}>
                        <RotateCw size={14} aria-hidden />{row.retryLabel}
                      </button>
                    )}
                    {row.canEnterManually && onManual && <button type="button" className="uz-check" disabled={disabled} onClick={onManual}>Enter details manually</button>}
                  </div>
                )}
                {open && (
                  <div id={logId} className="rr-panel uz-log">
                    {lines.length > 0 && <ul className="rr-notes">{lines.map((line) => <li key={line}><AlertTriangle size={14} aria-hidden /><span>{line}</span></li>)}</ul>}
                    {notes.length > 0 && <ReadNotes notes={notes} busy={disabled} onRetry={() => void onRetry(row.id)} />}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {found.length > 0 && (
        <div className="uz-found-block">
          <p className="uz-chips-head" id="uz-chips-head">Found in your files</p>
          <ul className="uz-chips" aria-labelledby="uz-chips-head" aria-live="polite">
            {found.map((chip) => (
              <li key={chip.kind} className={`uz-chip is-${chip.state}`}>
                {chip.state === 'done' ? <Check size={14} strokeWidth={3} aria-hidden /> : chip.state === 'reading' ? <LoaderCircle size={14} className="ac-spin" aria-hidden /> : <AlertTriangle size={14} aria-hidden />}
                <span>{chip.label}</span>
                {chip.detail && <small>{chip.detail}</small>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
