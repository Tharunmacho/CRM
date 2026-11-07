import { fileHref } from '@/shared/api/client'
import { identLines, readableReference } from '../paper-model'
import type { ApplicationFormData, AssignmentData, FormDocument } from '../types'

/**
 * The paper form's header: logo, company, form title, and the photo box in the corner. It is read only: the photo
 * is uploaded from the Fill details view, and what arrives shows here at once.
 */
export function Letterhead({
  photo,
  reference,
  header,
  assignment,
}: {
  header: ApplicationFormData['header']
  photo: FormDocument | null
  reference: string
  /** Client, job, source and agency are printed under the address, so the PDF carries them. Only what is known is shown. */
  assignment?: AssignmentData
}) {
  const lines = identLines(assignment)
  return (
    <header className="af-letterhead">
      <div className="af-brand">
        <img className="af-logo is-light" src="/brand/care-india-logo-tight.png" alt="Care India Overseas" width={768} height={400} decoding="async" draggable={false} />
        <img className="af-logo is-dark" src="/brand/care-india-logo-tight-dark.png" alt="" width={768} height={400} decoding="async" draggable={false} />
      </div>
      <div className="af-heading">
        <p className="af-company">{header.company}</p>
        <h1 className="af-form-title">{header.title}</h1>
        {readableReference(reference) && <p className="af-ref">Reference <span className="tnum">{readableReference(reference)}</span></p>}
      </div>
      <div className="af-photo-wrap">
        <div className="af-photo" data-has-photo={photo ? 'true' : 'false'}>
          {photo?.url ? <img src={fileHref(photo.url)} alt="Photo" /> : <span className="af-photo-hint">Photo</span>}
        </div>
      </div>
      <p className="af-address">
        <span>{header.address}</span>
        <span>Phone: {header.phone} | Email: {header.email}</span>
      </p>
      {lines.length > 0 && (
        <dl className="af-ident" aria-label="Client, job, source and agency">
          {lines.map((item) => <div key={item.key}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}
        </dl>
      )}
    </header>
  )
}
