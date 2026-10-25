import { useState } from 'react'
import { AlertTriangle, ChevronRight } from 'lucide-react'
import type { OcrScan } from '@/shared/api/client'
import { hasOcrExtraction, inspectedPageCount, scanWarnings, type ScanKind } from '../ocr-form'

const KIND_TITLE: Record<ScanKind, string> = { resume: 'Resume', passport: 'Passport', aadhaar: 'Aadhaar' }

/** What one document gave us: the verdict, the fields it filled in (to double-check), warnings, and the page-by-page detail. */
export function DocumentReadReport({ kind, scan, applied }: { kind: ScanKind; scan: OcrScan; applied: string[] }) {
  const [showPages, setShowPages] = useState(false)
  const label = KIND_TITLE[kind]
  const inspection = scan.inspection
  const extracted = hasOcrExtraction(scan)
  const warnings = scanWarnings(scan)
  const selected = inspection?.selected_pages
    ?? (kind === 'resume' ? inspection?.resume_pages : kind === 'passport' ? inspection?.passport_pages : inspection?.aadhaar_pages)
  const list = (pages: number[] | undefined) => pages?.length ? pages.join(', ') : 'None'
  const title = scan.extraction_status === 'no_resume' ? 'No resume found' : !extracted ? 'Please check' : 'Details read'

  return (
    <section aria-label={`What we read from the ${label.toLowerCase()}`} className={`ac-report ${extracted ? 'is-ok' : 'is-warn'}`}>
      <p className="ac-report-head">
        <strong>{label}</strong>
        <span className={`ac-pill ${extracted ? 'is-good' : 'is-warn'}`}><i aria-hidden />{title}</span>
      </p>
      <p className="ac-report-copy">
        {extracted
          ? 'Check the fields marked in the form before you save. Anything you typed yourself is kept.'
          : 'We could not fill in any details. Try a clearer file, or type the details yourself.'}
      </p>
      {applied.length > 0 && (
        <div className="ac-applied">
          <span className="ac-applied-label">Filled from this document</span>
          <ul>{applied.map((name) => <li key={name}>{name}</li>)}</ul>
        </div>
      )}
      {warnings.length > 0 && (
        <ul className="ac-report-warnings">
          {warnings.map((warning, index) => (
            <li key={index}><AlertTriangle aria-hidden size={14} /><span>{warning}</span></li>
          ))}
        </ul>
      )}
      {inspection && (
        <div className="ac-report-details">
          <button type="button" className="ac-report-toggle" aria-expanded={showPages} onClick={() => setShowPages((open) => !open)}>
            <ChevronRight size={14} aria-hidden />Page details
          </button>
          {showPages && <div className="ac-report-more">
            <dl>
              <div><dt>Pages checked</dt><dd>{inspectedPageCount(inspection)} of {inspection.page_count}</dd></div>
              <div><dt>Pages used for the {kind}</dt><dd>{list(selected)}</dd></div>
              <div><dt>Pages that look like a resume</dt><dd>{list(inspection.resume_pages)}</dd></div>
              <div><dt>Pages we could not read</dt><dd>{list(inspection.unread_pages)}</dd></div>
              <div><dt>Pages we are not sure about</dt><dd>{list(inspection.unknown_pages)}</dd></div>
            </dl>
            <p>Page numbers match the original file. A page we checked may still be hard to read or unclear.</p>
            {inspection.pages.length > 0 && (
              <ol className="ac-report-pages">
                {inspection.pages.map((page) => (
                  <li key={page.page_number}>
                    <strong>Page {page.page_number}</strong>
                    {' · '}{page.status}; {page.document_type === 'unknown' ? 'type not clear' : page.document_type}
                    {page.reason && <p>{page.reason}</p>}
                  </li>
                ))}
              </ol>
            )}
          </div>}
        </div>
      )}
    </section>
  )
}
