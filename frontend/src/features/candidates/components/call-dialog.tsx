import { useEffect, useState } from 'react'
import { useStore } from '@/shared/state/crm-store'
import { api } from '@/shared/api/client'
import { Dialog } from '@/shared/ui/overlay'
import { Button, Field, RadioRow, Textarea } from '@/shared/ui/primitives'
import { callBody, callCanSave, initialCallForm } from '@/features/candidates/call-outcome'

/** What the server says after a call result is saved; `notice` is a plain-English line worth showing. */
interface CallSaved { notice?: string | null }

/** The call-result dialog (called / interested / remarks). Shared by the candidate profile and the match dialog. */
export function CallDialog({
  open,
  onClose,
  candidateId,
  initialInterested = '',
}: {
  open: boolean
  onClose: () => void
  candidateId: string
  /** Opens with this answer chosen ('no' = not interested, 'rejected'); the Tasks list's row menu uses it for a candidate who has no job yet. */
  initialInterested?: 'yes' | 'no' | 'rejected' | 'unreachable' | ''
}) {
  const store = useStore()
  const [interested, setInterested] = useState<string>(initialInterested)
  const [remarks, setRemarks] = useState('')
  const [called, setCalled] = useState('yes')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  // One shared dialog serves every row: every opening (and every other candidate) starts clean, so remarks or a "Called: No" typed for one
  // person can never reach another person's record.
  useEffect(() => {
    if (!open) return
    const start = initialCallForm(initialInterested)
    setInterested(start.interested); setCalled(start.called); setRemarks(start.remarks); setError(start.error)
  }, [open, candidateId, initialInterested])

  const save = async () => {
    if (saving) return
    setSaving(true); setError('')
    try {
      const saved = await api.post<CallSaved | null>(`/candidates/${candidateId}/call`, callBody(called, interested, remarks))
      const notice = typeof saved?.notice === 'string' ? saved.notice.trim() : ''
      try {
        await store.refresh({ keepCurrent: true })
        store.notify(notice || (interested === 'no' ? 'Marked not interested. The candidate is on the Declined tab of the Tasks page.' : interested === 'rejected' ? 'Rejected. The candidate is on the Rejected tab of the Tasks page.' : 'Call outcome recorded'))
      } catch {
        store.notify('Saved. The workspace could not refresh; reload to see the latest records.', 'warn')
      }
      setInterested(''); setRemarks(''); setCalled('yes'); onClose()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'We could not save the call result. Please try again.')
    } finally { setSaving(false) }
  }

  return (
    <Dialog
      open={open}
      onClose={() => { if (!saving) onClose() }}
      title="Record the call result"
      description="If the candidate is interested, not interested or rejected, the initial call task is done. No client or job is needed here: choose one from Forwarded to client onward. If they did not answer, or you have not called yet, the task stays open."
      footer={
        <>
          <Button variant="ghost" disabled={saving} onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={saving || !callCanSave(called, interested)} onClick={() => void save()}>
            {saving ? 'Saving…' : 'Save result'}
          </Button>
        </>
      }
    >
      <fieldset disabled={saving} className="min-w-0 space-y-4">
        <Field label="Called" required><RadioRow name="called" value={called} onChange={value => { setCalled(value); if (value === 'no') setInterested('unreachable') }} options={[{ value: 'yes', label: 'Yes' }, { value: 'no', label: 'No' }]} /></Field>
        <Field label="Interested" required>
          <RadioRow
            name="interested"
            value={interested}
            onChange={setInterested}
            options={called === 'no' ? [{ value: 'unreachable', label: 'Not contacted yet' }] : [
              { value: 'yes', label: 'Yes' },
              { value: 'no', label: 'Not interested (declined)' },
              { value: 'rejected', label: 'Rejected (not suitable)' },
              { value: 'unreachable', label: 'Not reachable' },
            ]}
          />
        </Field>
        {error && <p role="alert" className="text-sm text-stamp-600">{error}</p>}
        <Field label="Remarks">
          <Textarea
            maxLength={10000}
            value={remarks}
            onChange={(e) => setRemarks(e.target.value)}
            placeholder="What the candidate said"
          />
        </Field>
      </fieldset>
    </Dialog>
  )
}
