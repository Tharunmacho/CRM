import { useMemo } from 'react'
import { ExternalLink } from 'lucide-react'
import { Button } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { toDraft } from '../form-model'
import type { ApplicationFormData } from '../types'
import { FormSheet } from './form-sheet'
import type { FormContext } from './sections'

/**
 * A read-only look at the paper form for the candidate being entered, built from what is typed and read so far.
 * Once the candidate is saved the same button opens the real, editable form.
 */
export function EntryFormPreview({ open, onClose, form, savedCandidateId, onOpenSaved }: {
  open: boolean
  onClose: () => void
  form: ApplicationFormData
  savedCandidateId: string | null
  onOpenSaved: () => void
}) {
  const ctx: FormContext = useMemo(() => ({
    form, draft: toDraft(form), editing: false, errors: {}, updated: [], setDraft: () => {},
  }), [form])
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Application form preview"
      description={savedCandidateId ? 'This candidate is saved. Open the live form to edit it or print it.' : 'This is how the form looks with what you have entered so far. Save the candidate to open the live form.'}
      width="full"
      footer={<>
        <Button variant="ghost" onClick={onClose}>Close preview</Button>
        <Button variant="primary" disabled={!savedCandidateId} icon={<ExternalLink />} onClick={onOpenSaved}>{savedCandidateId ? 'Open application form' : 'Save to open the live form'}</Button>
      </>}
    >
      <div className="af af-embed af-preview">
        <FormSheet ctx={ctx} missing={form.completeness.missing} canUpload={false} upload={async () => {}} busyKinds={[]} canOpenStage={false} onOpenStage={() => {}} />
      </div>
    </Dialog>
  )
}
