import { CheckCircle2, FileText, IdCard, Image as ImageIcon, Loader2, TriangleAlert, type LucideIcon } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { dateTimeLabel } from '@/shared/lib/format'
import { FileDrop } from '@/shared/ui/file-drop'
import { DOCUMENT_KINDS, DOCUMENT_LABELS, describeExtraction } from '../form-model'
import type { ApplicationFormData, DocumentKind, DocumentSlot } from '../types'
import { Section } from './sections'
import '@/styles/application-form-aadhaar.css'

const ICONS: Record<DocumentKind, LucideIcon> = { resume: FileText, passport: IdCard, photo: ImageIcon }
const HELP: Record<DocumentKind, string> = {
  resume: 'Name, contact, education and experience are read from it.',
  passport: 'Number, dates, place of issue and father\'s name are read from it.',
  photo: 'Shown in the photo box at the top of the form.',
}

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
  const missing = DOCUMENT_KINDS.filter((kind) => !form.documents[kind].uploaded).map((kind) => ({ key: `documents.${kind}`, label: kind, section: 5, kind: 'document' as const, message: '' }))
  return (
    <Section no={5} missing={missing}>
      <div className="af-docs">
        {DOCUMENT_KINDS.map((kind) => {
          const slot = form.documents[kind]
          const document = slot.current
          const Icon = ICONS[kind]
          return (
            <div className={cn('af-doc', !document && 'is-missing')} key={kind} data-document={kind}>
              <div className="af-doc-title">
                <Icon aria-hidden="true" />
                <b>{DOCUMENT_LABELS[kind]}</b>
                {!document && <span className="af-chip is-missing">Not uploaded</span>}
              </div>
              <FileDrop
                variant="row"
                label={DOCUMENT_LABELS[kind]}
                icon={Icon}
                value={document ? { name: document.fileName, sizeKb: document.sizeKb, id: document.attachmentId, url: document.url, version: document.version } : null}
                onFile={(file) => onUpload(kind, file)}
                disabled={!canUpload}
                busy={busyKinds.includes(kind)}
                busyLabel="uploading…"
                help={document ? undefined : HELP[kind]}
              />
              {document?.uploadedAt && <p className="af-doc-meta">Uploaded {dateTimeLabel(document.uploadedAt)}</p>}
              <ExtractionLine kind={kind} slot={slot} />
            </div>
          )
        })}
      </div>
      {form.aadhaarMasked && (
        <p className="af-doc-meta af-aadhaar" data-testid="aadhaar-masked">
          <IdCard aria-hidden="true" />Aadhaar copy read: <b className="ident">{form.aadhaarMasked}</b>. Only the last four digits are shown. Name, date of birth, gender, address and father&apos;s name were read from it.
        </p>
      )}
    </Section>
  )
}
