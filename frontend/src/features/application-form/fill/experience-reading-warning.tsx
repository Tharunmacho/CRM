import { AlertCircle } from 'lucide-react'
import { incompleteReadingText } from '@/features/candidates/read-report'
import type { DocumentSlot, Extraction } from '../types'

/** Coverage describes the reading, not whether staff have filled every form field. */
export function ExperienceReadingWarning({ slot }: { slot: DocumentSlot | undefined }) {
  const readings: { fileName?: string; extraction?: Extraction | null }[] = slot?.multiple && slot.files.length
    ? slot.files
    : [{ fileName: slot?.current?.fileName, extraction: slot?.extraction }]
  const warnings = readings.flatMap(({ fileName, extraction }) => {
    if (!extraction) return []
    const coverage = incompleteReadingText(extraction.coverage)
    const message = coverage ?? (extraction.status === 'processing' ? 'The resume is still being read.'
      : ['failed', 'unreadable', 'needs_review'].includes(extraction.status) ? 'The resume reading needs checking.' : null)
    return message ? [{ fileName, message }] : []
  })
  if (slot?.extractionPending) warnings.unshift({ fileName: undefined, message: 'Resume files are saved and waiting for extraction.' })
  if (!warnings.length) return null
  return (
    <aside className="aff-reading-warning" aria-label="Experience reading review">
      <AlertCircle size={18} aria-hidden="true" />
      <div>
        <strong>Check experience totals</strong>
        {warnings.map(({ fileName, message }, index) => <p key={`${fileName}-${index}`}>{warnings.length > 1 && fileName ? `${fileName}: ` : ''}{message} Employers or dates may be missing.</p>)}
        <a href="#af-documents">Review the resume and reading details</a>
      </div>
    </aside>
  )
}
