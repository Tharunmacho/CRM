import { useRef, useState, type ReactNode } from 'react'
import {
  Briefcase, CheckCircle2, Clock, Download, Eye, FileSignature, FileText, Files, GraduationCap, IdCard, Image as ImageIcon, Landmark,
  Loader2, Paperclip, Plane, ShieldCheck, Stamp, Star, Stethoscope, Upload, type LucideIcon,
} from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { dateTimeLabel } from '@/shared/lib/format'
import { fileHref } from '@/shared/api/client'
import { FileLink } from '@/shared/ui/file-drop'
import { DOCUMENT_ACCEPT } from '@/shared/lib/document-files'
import { splitSourceCaption } from '@/shared/lib/split-source'
import { DocumentUploadQueue, useDocumentUploadQueue } from '@/shared/ui/document-upload-queue'
import { Button, Field, Textarea } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { documentLabel, documentSlot } from '@/shared/model/document-slots'
import { extractionLineKey, routeLineNotes, sizeLabel, slotReadState } from '../form-model'
import { loadDocumentReport, type IdentityCheck } from '../form-api'
import { documentRowStates, documentRowsProgress, documentsCountText, groupDocumentRows, groupProgressText, type DocumentRowIcon, type DocumentRowState } from '../document-rows'
import type { ApplicationFormData, DocumentKind, DocumentSlot, FormDocument } from '../types'
import { SectionHeader } from './section-header'
import { ExtractionLine } from './extraction-line'
import { SlotReadingNote } from './slot-reading'
import { ReadReport } from '@/features/candidates/components/read-report'
import { formReports } from '@/features/candidates/read-report'
import '@/styles/application-form-aadhaar.css'
import '@/styles/application-form-v2.css'

const ICONS: Record<string, LucideIcon> = {
  resume: FileText, passport: IdCard, aadhaar: IdCard, photo: ImageIcon, education_certificate: GraduationCap, experience_certificate: Briefcase,
  signed_offer: FileSignature, medical: Stethoscope, pcc: ShieldCheck, visa: Stamp, google_review: Star, visa_stamping: Stamp,
  emigration: Landmark, air_ticket: Plane, other: Paperclip,
}

const STATE_ICONS: Record<DocumentRowIcon, LucideIcon | null> = { check: CheckCircle2, clock: Clock, dot: null }

/** Copies the plain-text report of how this candidate's documents were read (no names or numbers) for pasting to the developer. */
function CopyReportButton({ candidateId }: { candidateId: string }) {
  const [state, setState] = useState<'idle' | 'working' | 'copied' | 'failed'>('idle')
  const copy = async () => {
    setState('working')
    try {
      await navigator.clipboard.writeText(await loadDocumentReport(candidateId))
      setState('copied')
    } catch {
      setState('failed')
    }
    window.setTimeout(() => setState('idle'), 3000)
  }
  return (
    <button type="button" className="af-docbtn" disabled={state === 'working'} onClick={() => void copy()} title="Copy how these documents were read, to send to the developer">
      {state === 'working' ? 'Copying…' : state === 'copied' ? 'Report copied' : state === 'failed' ? 'Could not copy' : 'Copy report'}
    </button>
  )
}

/** One stored file on one line: its name (a link), then size and date, muted. The name is cut with an ellipsis, never wrapped. */
function FileRow({ file, action }: { file: FormDocument; action?: ReactNode }) {
  const detail = [sizeLabel(file.sizeKb), file.uploadedAt ? dateTimeLabel(file.uploadedAt) : 'Saving'].join(' · ')
  const splitNote = splitSourceCaption(file.splitFrom)
  return (
    <div className="af-docfile">
      <div className="af-docfile-copy">{file.url ? <a href={fileHref(file.url)} target="_blank" rel="noreferrer" title={file.fileName}>{file.fileName}</a> : <span title={file.fileName}>{file.fileName}</span>}<small>{detail}</small>{splitNote && <small className="af-docfile-split" title={splitNote}>{splitNote}</small>}</div>
      {file.url && <div className="af-docfile-actions"><a href={fileHref(file.url)} target="_blank" rel="noreferrer" aria-label={`View ${file.fileName}`} title={`View ${file.fileName}`} className="af-docbtn is-icon"><Eye aria-hidden="true" /></a><FileLink url={file.url} download title={`Download ${file.fileName}`} className="af-docbtn is-icon"><Download aria-hidden="true" /><span className="sr-only">Download {file.fileName}</span></FileLink>{action || <span aria-hidden="true" />}</div>}
    </div>
  )
}

/** A hidden file input plus drag-and-drop for one row; the row decides what is shown. */
function useSlotUpload(slot: DocumentSlot, canUpload: boolean, busy: boolean, onUpload: (kind: DocumentKind, file: File) => Promise<void>) {
  const input = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const imageOnly = documentSlot(slot.slot)?.imageOnly === true
  const queue = useDocumentUploadQueue((file) => onUpload(slot.slot, file), { multiple: slot.multiple, disabled: !canUpload || busy, validate: (file) => imageOnly && !/^image\//.test(file.type) && !/\.(jpe?g|png|webp|heic)$/i.test(file.name) ? 'Choose a JPG, PNG, WebP or HEIC photo.' : null })
  const blocked = !canUpload || busy || queue.busy
  const accept = imageOnly ? 'image/jpeg,image/png,image/webp,image/heic,.jpg,.jpeg,.png,.webp,.heic' : DOCUMENT_ACCEPT
  const drop = {
    onDragOver: (event: React.DragEvent) => { event.preventDefault(); if (!blocked) setDragging(true) },
    onDragLeave: () => setDragging(false),
    onDrop: (event: React.DragEvent) => { event.preventDefault(); setDragging(false); if (!blocked) void queue.take(event.dataTransfer.files) },
  }
  return { input, dragging, blocked, queue, take: queue.take, accept, drop, open: () => input.current?.click() }
}

/**
 * One document on one compact row, on fixed columns so the rows line up from top to bottom:
 * status icon, name, status chip, file (name, size, date), actions (Upload or Replace, View).
 */
function DocumentRow({ slot, state, canUpload, busy, onUpload, onManualStatus, onExtract, readError, onIdentity, lineNotes }: {
  slot: DocumentSlot
  state: DocumentRowState
  canUpload: boolean
  busy: boolean
  onUpload: (kind: DocumentKind, file: File) => Promise<void>
  onIdentity?: (attachmentId: string, action: IdentityCheck) => Promise<void>
  onManualStatus?: (kind: DocumentKind, uploaded: boolean, note: string) => Promise<void>
  /** Reads stored files again. Nobody starts a first reading: the form does that by itself; this is for Read again. */
  onExtract?: (attachmentIds: string[]) => Promise<void>
  /** Why the form could not start a reading by itself; empty when it could. */
  readError?: string
  /** The notes of every line, routed and de-duplicated across the list (`routeLineNotes`). */
  lineNotes: Record<string, string[]>
}) {
  const label = documentLabel(slot.slot)
  const [manualOpen, setManualOpen] = useState(false)
  const [manualNote, setManualNote] = useState('')
  const [manualBusy, setManualBusy] = useState(false)
  const [manualError, setManualError] = useState('')
  const upload = useSlotUpload(slot, canUpload && !state.disabled, busy, onUpload)
  const failedReads = slotReadState(slot).failed
  const readAgain = (attachmentId?: string) => (canUpload && onExtract && attachmentId && failedReads.includes(attachmentId) ? () => onExtract([attachmentId]) : undefined)
  const files = slot.multiple ? slot.files : slot.current ? [slot.current] : []
  const hasFile = files.some((file) => Boolean(file.url))
  const rejectedOnly = hasFile && files.every((file) => file.identityMismatch)
  const marked = slot.manualStatus?.uploaded === true && !hasFile
  const working = busy || upload.queue.busy
  const StateIcon = STATE_ICONS[state.icon] ?? ICONS[slot.slot] ?? FileText
  const mayUpload = canUpload
  const action = working ? 'Uploading' : slot.multiple ? 'Add files' : hasFile ? 'Replace' : 'Upload'
  const uploadButton = canUpload && <button type="button" className={cn('af-docbtn', !files.length && 'is-primary')} disabled={upload.blocked || manualBusy} aria-label={`${action} ${label}`} onClick={upload.open}>{working ? <Loader2 className="is-spin" aria-hidden="true" /> : <Upload aria-hidden="true" />}{action}</button>
  const saveManual = async () => {
    if (!onManualStatus || manualBusy) return
    setManualBusy(true); setManualError('')
    try { await onManualStatus(slot.slot, !marked, manualNote.trim()); setManualOpen(false) }
    catch (cause) { setManualError(cause instanceof Error ? cause.message : 'The document status was not saved.') }
    finally { setManualBusy(false) }
  }
  return (
    <li
      className={cn('af-docrow', `is-${state.key}`, hasFile && 'has-files', slot.multiple && 'is-multi-file', !slot.multiple && hasFile && 'is-single-file', upload.dragging && 'is-dragging', busy && 'is-busy')}
      data-document={slot.slot}
      aria-busy={working || manualBusy}
      {...(mayUpload ? upload.drop : {})}
    >
      <span className={cn('af-docrow-icon', `is-${state.key}`)} aria-hidden="true">{state.icon === 'dot' ? <i /> : <StateIcon />}</span>
      <b className="af-docrow-name" title={label}>{label}</b>
      <span className={cn('af-doc-chip', `is-${state.key}`, rejectedOnly && 'is-rejected')}>{working ? 'Uploading' : rejectedOnly ? 'Wrong document' : state.chip}</span>
      <div className="af-docrow-file">
        {files.map((file, index) => <div key={file.attachmentId}><FileRow file={file} action={index === 0 && hasFile ? uploadButton : undefined} />{slot.multiple && file.extraction && <ExtractionLine kind={slot.slot} slot={{ ...slot, extraction: file.extraction }} onCorrect={canUpload ? upload.open : undefined} disabled={upload.blocked} attachmentId={file.attachmentId} onIdentity={canUpload ? onIdentity : undefined} onReadAgain={readAgain(file.attachmentId)} notes={lineNotes[extractionLineKey(slot.slot, file.attachmentId)]} />}</div>)}
        {marked && <div className="af-doc-manual"><b>Marked manually · No file attached</b><small>{slot.manualStatus?.by.name || 'Former team member'}{slot.manualStatus?.at ? ` · ${dateTimeLabel(slot.manualStatus.at)}` : ''}</small>{slot.manualStatus?.note && <p>{slot.manualStatus.note}</p>}</div>}
        {slot.multiple && !hasFile && !marked && <p className="af-doc-hint">{slot.slot === 'passport' ? 'Add the photo page, address page or other passport pages together.' : slot.slot === 'aadhaar' ? 'Add both the front and back pages.' : 'Choose or drop several files.'}</p>}
        {(!slot.multiple || !files.some((file) => file.extraction)) && <ExtractionLine kind={slot.slot} slot={slot} onCorrect={canUpload ? upload.open : undefined} disabled={upload.blocked} attachmentId={slot.current?.attachmentId} onIdentity={canUpload ? onIdentity : undefined} onReadAgain={readAgain(slot.current?.attachmentId)} notes={lineNotes[extractionLineKey(slot.slot)]} />}
        <DocumentUploadQueue queue={upload.queue} />
        <SlotReadingNote slot={slot} enabled={canUpload && Boolean(onExtract)} error={readError ?? ''} onRead={onExtract} />
      </div>
      {!hasFile && <div className="af-docrow-actions">
        {uploadButton}
        {canUpload && onManualStatus && slot.manualStatusSupported && !hasFile && <button type="button" className="af-docbtn af-doc-manual-action" disabled={working || manualBusy} onClick={() => { setManualNote(''); setManualError(''); setManualOpen(true) }}>{marked ? 'Mark as pending' : 'Mark as uploaded'}</button>}
      </div>}
      {mayUpload && (
        <input
          ref={upload.input}
          type="file"
          multiple={slot.multiple}
          accept={upload.accept}
          className="sr-only"
          tabIndex={-1}
          aria-label={`Choose ${label}`}
          disabled={upload.blocked}
          onChange={(event) => { void upload.take(event.currentTarget.files); event.currentTarget.value = '' }}
        />
      )}
      {manualOpen && <Dialog open onClose={() => { if (!manualBusy) setManualOpen(false) }} title={`${marked ? 'Mark as pending' : 'Mark as uploaded'}: ${label}`} description={marked ? 'Return this document to the pending checklist.' : 'Update the checklist when you have received this document. A file can be added later.'} width="sm" footer={<><Button disabled={manualBusy} onClick={() => setManualOpen(false)}>Cancel</Button><Button variant="primary" disabled={manualBusy} onClick={() => void saveManual()}>{manualBusy ? 'Saving…' : marked ? 'Mark as pending' : 'Mark as uploaded'}</Button></>}><Field label="Note (optional)"><Textarea rows={3} maxLength={1000} value={manualNote} disabled={manualBusy} onChange={(event) => setManualNote(event.target.value)} /></Field>{manualError && <p role="alert" className="af-note is-error">{manualError}</p>}</Dialog>}
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
  onManualStatus,
  onExtract,
  readError,
  onIdentity,
}: {
  form: ApplicationFormData
  canUpload: boolean
  onUpload: (kind: DocumentKind, file: File) => Promise<void>
  onIdentity?: (attachmentId: string, action: IdentityCheck) => Promise<void>
  busyKinds: DocumentKind[]
  onManualStatus?: (kind: DocumentKind, uploaded: boolean, note: string) => Promise<void>
  onExtract?: (attachmentIds: string[]) => Promise<void>
  readError?: string
}) {
  const rows = documentRowStates(form, canUpload)
  const progress = documentRowsProgress(rows.map((row) => row.state))
  // What the document reader read and what it could not place (only when the server sent it; live with the form's refresh).
  const reads = formReports(rows.map(({ slot }) => slot))
  const lineNotes = routeLineNotes(form.documents)
  return (
    <section className="af-sec af-docs-card" id="af-documents" aria-labelledby="af-documents-title">
      <SectionHeader
        icon={<Files />}
        id="af-documents-title"
        title="Documents"
        chip={(
          <div className="af-docs-progress">
            {canUpload && form.candidate?.id && <CopyReportButton candidateId={form.candidate.id} />}
            <span className="af-docs-count tnum">{documentsCountText(progress)}</span>
            <span className={cn('af-docs-bar', progress.total > 0 && progress.uploaded === progress.total && 'is-full')} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress.percent} aria-label="Documents uploaded">
              <i style={{ width: `${progress.percent}%` }} />
            </span>
          </div>
        )}
      />
      {/* Four small groups, each a grid of compact cards: a name, a status chip and one action, so the whole list is seen at a glance. */}
      <div className="af-docgroups">
        {groupDocumentRows(rows).map((group) => (
          <section key={group.key} className="af-docgroup" aria-labelledby={`af-docgroup-${group.key}`}>
            <h3 id={`af-docgroup-${group.key}`}>{group.label}{groupProgressText(group.rows) && <small className="tnum">{groupProgressText(group.rows)}</small>}</h3>
            <ul className="af-doclist">
              {group.rows.map(({ slot, state }) => (
                <DocumentRow
                  key={slot.slot}
                  slot={slot}
                  state={state}
                  canUpload={canUpload}
                  busy={busyKinds.includes(slot.slot)}
                  onUpload={onUpload}
                  onManualStatus={onManualStatus}
                  onExtract={onExtract}
                  readError={readError}
                  onIdentity={onIdentity}
                  lineNotes={lineNotes}
                />
              ))}
            </ul>
          </section>
        ))}
      </div>
      {reads.map((item) => <ReadReport key={item.id} report={item.report} caption={reads.length > 1 ? item.caption : undefined} />)}
      {form.aadhaarMasked && (
        <p className="af-doc-meta af-aadhaar" data-testid="aadhaar-masked">
          <IdCard aria-hidden="true" />Aadhaar copy read: <b className="ident">{form.aadhaarMasked}</b>. Only the last four digits are shown.
        </p>
      )}
    </section>
  )
}
