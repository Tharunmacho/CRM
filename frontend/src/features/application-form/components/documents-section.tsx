import { useRef, useState } from 'react'
import {
  Briefcase, CheckCircle2, Clock, Eye, FileSignature, FileText, Files, GraduationCap, IdCard, Image as ImageIcon, Landmark,
  Loader2, Paperclip, Plane, ShieldCheck, Stamp, Star, Stethoscope, TriangleAlert, Upload, type LucideIcon,
} from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { dateTimeLabel } from '@/shared/lib/format'
import { fileHref } from '@/shared/api/client'
import { DOCUMENT_ACCEPT, documentFileError } from '@/shared/lib/document-files'
import { documentLabel, documentSlot } from '@/shared/model/document-slots'
import { describeExtraction, sizeLabel } from '../form-model'
import { documentRowStates, documentRowsProgress, documentsCountText, type DocumentRowIcon, type DocumentRowState } from '../document-rows'
import type { ApplicationFormData, DocumentKind, DocumentSlot, FormDocument } from '../types'
import { SectionHeader } from './section-header'
import { ReadReport } from '@/features/candidates/components/read-report'
import { formReports } from '@/features/candidates/read-report'
import '@/styles/application-form-aadhaar.css'
import '@/styles/application-form-v2.css'

const ICONS: Record<string, LucideIcon> = {
  resume: FileText, passport: IdCard, photo: ImageIcon, education_certificate: GraduationCap, experience_certificate: Briefcase,
  signed_offer: FileSignature, medical: Stethoscope, pcc: ShieldCheck, visa: Stamp, google_review: Star, visa_stamping: Stamp,
  emigration: Landmark, air_ticket: Plane, other: Paperclip,
}

const STATE_ICONS: Record<DocumentRowIcon, LucideIcon | null> = { check: CheckCircle2, clock: Clock, dot: null }

/** What the reading of one document did, in the words a recruiter needs. */
export function ExtractionLine({ kind, slot }: { kind: DocumentKind; slot: DocumentSlot }) {
  const note = describeExtraction(kind, slot)
  if (note.tone === 'none') return null
  const Icon = note.tone === 'reading' ? Loader2 : note.tone === 'done' ? CheckCircle2 : TriangleAlert
  return (
    <p className={cn('af-extract', `is-${note.tone}`)} role={note.tone === 'failed' ? 'alert' : 'status'}>
      <Icon aria-hidden="true" />{note.text}
    </p>
  )
}

/** One stored file on one line: its name (a link), then size and date, muted. The name is cut with an ellipsis, never wrapped. */
function FileRow({ file }: { file: FormDocument }) {
  const detail = [sizeLabel(file.sizeKb), file.uploadedAt ? dateTimeLabel(file.uploadedAt) : 'Saving'].join(' · ')
  return (
    <p className="af-docfile">
      {file.url ? <a href={fileHref(file.url)} target="_blank" rel="noreferrer" title={file.fileName}>{file.fileName}</a> : <span title={file.fileName}>{file.fileName}</span>}
      <small>{detail}</small>
    </p>
  )
}

/** A hidden file input plus drag-and-drop for one row; the row decides what is shown. */
function useSlotUpload(slot: DocumentSlot, canUpload: boolean, busy: boolean, onUpload: (kind: DocumentKind, file: File) => Promise<void>) {
  const input = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState('')
  const imageOnly = documentSlot(slot.slot)?.imageOnly === true
  const blocked = !canUpload || busy
  const take = async (files: FileList | null) => {
    const file = files?.[0]
    if (!file || blocked) return
    const invalid = documentFileError(file)
    if (invalid) { setError(invalid); return }
    if (imageOnly && !/^image\//.test(file.type) && !/\.(jpe?g|png|webp|heic)$/i.test(file.name)) { setError('Choose a JPG, PNG, WebP or HEIC photo.'); return }
    setError('')
    try { await onUpload(slot.slot, file) } catch (cause) { setError(cause instanceof Error ? cause.message : 'The file was not uploaded. Please try again.') }
  }
  const accept = imageOnly ? 'image/jpeg,image/png,image/webp,image/heic,.jpg,.jpeg,.png,.webp,.heic' : DOCUMENT_ACCEPT
  const drop = {
    onDragOver: (event: React.DragEvent) => { event.preventDefault(); if (!blocked) setDragging(true) },
    onDragLeave: () => setDragging(false),
    onDrop: (event: React.DragEvent) => { event.preventDefault(); setDragging(false); void take(event.dataTransfer.files) },
  }
  return { input, dragging, error, blocked, take, accept, drop, open: () => input.current?.click() }
}

/**
 * One document on one compact row, on fixed columns so the rows line up from top to bottom:
 * status icon, name, status chip, file (name, size, date), actions (Upload or Replace, View).
 */
function DocumentRow({ slot, state, canUpload, busy, onUpload }: {
  slot: DocumentSlot
  state: DocumentRowState
  canUpload: boolean
  busy: boolean
  onUpload: (kind: DocumentKind, file: File) => Promise<void>
}) {
  const label = documentLabel(slot.slot)
  const upload = useSlotUpload(slot, canUpload && !state.disabled, busy, onUpload)
  const files = slot.multiple ? slot.files : slot.current ? [slot.current] : []
  const StateIcon = STATE_ICONS[state.icon] ?? ICONS[slot.slot] ?? FileText
  const mayUpload = canUpload
  const action = busy ? 'Uploading' : slot.multiple ? 'Add' : files.length ? 'Replace' : 'Upload'
  return (
    <li
      className={cn('af-docrow', `is-${state.key}`, upload.dragging && 'is-dragging', busy && 'is-busy')}
      data-document={slot.slot}
      aria-busy={busy}
      {...(mayUpload ? upload.drop : {})}
    >
      <span className={cn('af-docrow-icon', `is-${state.key}`)} aria-hidden="true">{state.icon === 'dot' ? <i /> : <StateIcon />}</span>
      <b className="af-docrow-name" title={label}>{label}</b>
      <span className={cn('af-doc-chip', `is-${state.key}`)}>{state.chip}</span>
      <div className="af-docrow-file">
        {files.map((file) => <FileRow key={file.attachmentId} file={file} />)}
        {busy && <span className="af-docrow-progress" role="progressbar" aria-label={`Uploading ${label}`}><i /></span>}
        <ExtractionLine kind={slot.slot} slot={slot} />
        {upload.error && <p className="af-note is-error" role="alert">{upload.error}</p>}
      </div>
      <div className="af-docrow-actions">
        {!slot.multiple && slot.current?.url && (
          <a className="af-docbtn is-icon" href={fileHref(slot.current.url)} target="_blank" rel="noreferrer" aria-label={`View ${label}`} title={`View ${label}`}><Eye aria-hidden="true" /></a>
        )}
        {canUpload && (
          <button
            type="button"
            className={cn('af-docbtn', !files.length && 'is-primary')}
            disabled={upload.blocked}
            aria-label={`${action} ${label}`}
            onClick={upload.open}
          >
            {busy ? <Loader2 className="is-spin" aria-hidden="true" /> : <Upload aria-hidden="true" />}
            {action}
          </button>
        )}
      </div>
      {mayUpload && (
        <input
          ref={upload.input}
          type="file"
          accept={upload.accept}
          className="sr-only"
          tabIndex={-1}
          aria-label={`Choose ${label}`}
          disabled={upload.blocked}
          onChange={(event) => { void upload.take(event.currentTarget.files); event.currentTarget.value = '' }}
        />
      )}
    </li>
  )
}

/**
 * The Documents section of the working card: a slim progress bar and "N of M documents" (every document that is not optional), then
 * one row per document in the shared order. Nothing is locked: any document can be uploaded at any time. The Passport row is a row like the others (the passport details are section 2 of the page).
 */
export function DocumentsSection({
  form,
  canUpload,
  onUpload,
  busyKinds,
}: {
  form: ApplicationFormData
  canUpload: boolean
  onUpload: (kind: DocumentKind, file: File) => Promise<void>
  busyKinds: DocumentKind[]
}) {
  const rows = documentRowStates(form, canUpload)
  const progress = documentRowsProgress(rows.map((row) => row.state))
  // What the document reader read and what it could not place (only when the server sent it; live with the form's refresh).
  const reads = formReports(rows.map(({ slot }) => slot))
  return (
    <section className="af-sec af-docs-card" id="af-documents" aria-labelledby="af-documents-title">
      <SectionHeader
        icon={<Files />}
        id="af-documents-title"
        title="Documents"
        chip={(
          <div className="af-docs-progress">
            <span className="af-docs-count tnum">{documentsCountText(progress)}</span>
            <span className={cn('af-docs-bar', progress.total > 0 && progress.uploaded === progress.total && 'is-full')} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress.percent} aria-label="Documents uploaded">
              <i style={{ width: `${progress.percent}%` }} />
            </span>
          </div>
        )}
      />
      <ul className="af-doclist">
        {rows.map(({ slot, state }) => (
          <DocumentRow
            key={slot.slot}
            slot={slot}
            state={state}
            canUpload={canUpload}
            busy={busyKinds.includes(slot.slot)}
            onUpload={onUpload}
          />
        ))}
      </ul>
      {reads.map((item) => <ReadReport key={item.id} report={item.report} caption={reads.length > 1 ? item.caption : undefined} />)}
      {form.aadhaarMasked && (
        <p className="af-doc-meta af-aadhaar" data-testid="aadhaar-masked">
          <IdCard aria-hidden="true" />Aadhaar copy read: <b className="ident">{form.aadhaarMasked}</b>. Only the last four digits are shown.
        </p>
      )}
    </section>
  )
}
