import { AlertTriangle } from 'lucide-react'
import { Badge } from '@/shared/ui/display'
import { Button } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import type { CandidateEntryForm } from '../entry-model'
import type { DuplicateMatch } from '../entry-signals'
import type { useDocumentIntake } from '../use-document-intake'
import { EntryAvatar } from './entry-avatar'

/**
 * The duplicate decision: the candidate already on file next to what was just typed, and three clear ways out:
 * Cancel (keep typing), Open the existing candidate, or Combine into it. Combining is open to every role.
 */
export function DuplicateDialog({ duplicate, form, saving, error, onCancel, onOpen, onMerge }: {
  duplicate: { candidate: DuplicateMatch; matchedOn: string[] } | null
  form: CandidateEntryForm
  saving: boolean
  error: string
  onCancel: () => void
  onOpen: (id: string) => void
  onMerge: (id: string) => void
}) {
  return (
    <Dialog
      open={!!duplicate}
      onClose={() => { if (!saving) onCancel() }}
      title="This candidate is already in the Data Bank"
      description={
        duplicate
          ? `The ${duplicate.matchedOn.join(' and ')} already ${duplicate.matchedOn.length > 1 ? 'belong' : 'belongs'} to a candidate in the Data Bank. You can combine this entry with that candidate, or cancel.`
          : undefined
      }
      footer={
        <div className="ac-dialog-actions">
          <Button variant="ghost" disabled={saving} onClick={onCancel}>
            Cancel
          </Button>
          <Button disabled={saving} onClick={() => duplicate && onOpen(duplicate.candidate.id)}>
            Open existing candidate
          </Button>
          <Button variant="primary" disabled={saving} onClick={() => duplicate && onMerge(duplicate.candidate.id)}>{saving ? 'Combining…' : 'Combine with existing'}</Button>
        </div>
      }
    >
      {duplicate && (
        <div className="ac-dup">
          <div className="ac-dup-card is-existing">
            <EntryAvatar name={duplicate.candidate.name} size="lg" />
            <div className="ac-dup-body">
              <p className="ac-dup-label">Already in the Data Bank</p>
              <p className="ac-dup-name">{duplicate.candidate.name}</p>
              <p className="ac-dup-meta ident" title={duplicate.candidate.phone}>{duplicate.candidate.phone}</p>
              <div className="ac-dup-badges">
                <Badge>{duplicate.candidate.ref}</Badge>
                <Badge>Recruiter: {duplicate.candidate.ownerName ?? 'Not assigned yet'}</Badge>
                {duplicate.matchedOn.map((field) => <Badge key={field} tone="amber">Same {field}</Badge>)}
              </div>
            </div>
          </div>
          <div className="ac-dup-card">
            <EntryAvatar name={form.name.trim() || 'New candidate'} size="lg" />
            <div className="ac-dup-body">
              <p className="ac-dup-label">What you typed</p>
              <p className="ac-dup-name">{form.name.trim() || 'Unnamed'}</p>
              <p className="ac-dup-meta ident">{form.phone.trim() || 'No phone'}</p>
              {form.email.trim() && <p className="ac-dup-meta" title={form.email.trim()}>{form.email.trim()}</p>}
              {form.passportNo.trim() && <p className="ac-dup-meta ident">Passport {form.passportNo.trim()}</p>}
            </div>
          </div>
        </div>
      )}
      {duplicate && (
        <p className="ac-dup-note">
          <AlertTriangle size={14} aria-hidden />
          <span>The candidate stays with {duplicate.candidate.ownerName ?? 'the current recruiter'}. Only empty details are filled in; details already saved are not changed. Your documents are added as new copies. The stages stay as they are. No new task is created.</span>
        </p>
      )}
      {error && <p role="alert" className="mt-3 text-sm text-stamp-600">{error}</p>}
    </Dialog>
  )
}

/** Asked before a saved upload replaces what is on the form. */
export function RecoveryDialog({ open, recovering, hasFiles, intake, onStay, onOpen }: {
  open: boolean
  recovering: boolean
  hasFiles: boolean
  intake: ReturnType<typeof useDocumentIntake>
  onStay: () => void
  onOpen: () => void
}) {
  return (
    <Dialog
      open={open}
      onClose={() => { if (!recovering) onStay() }}
      title="Open a different saved upload?"
      description={hasFiles ? 'Your current form and files will be kept as their own saved upload. The one you pick will open with its own details.' : 'Your current form has no files and is not backed up. Opening the saved upload will replace what you typed.'}
      footer={<div className="ac-dialog-actions"><Button disabled={recovering} onClick={onStay}>Stay on this form</Button><Button variant="primary" disabled={recovering} onClick={onOpen}>{recovering ? 'Opening…' : 'Open saved upload'}</Button></div>}
    >
      <p className="text-sm text-ink-500">Details of different candidates are kept apart. Check the name and contact details before you save.</p>
      {intake.draftError && <p role="alert" className="mt-3 text-sm text-stamp-600">{intake.draftError}</p>}
    </Dialog>
  )
}
