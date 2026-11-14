import { useState } from 'react'
import { UserMinus } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { Button } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { unassignCopy } from '@/features/candidates/data-bank-pools'

/** Admin only: take candidates back from their recruiter so they wait in "Not assigned" again. Says what will happen before it does. */
export function UnassignCandidatesDialog({ candidateIds, onClose, onSaved }: {
  candidateIds: string[]
  onClose: () => void
  onSaved?: () => void
}) {
  const store = useStore()
  const [busy, setBusy] = useState(false)
  const ids = [...new Set(candidateIds)]
  const only = ids.length === 1 ? store.candidate(ids[0]) : undefined
  const copy = unassignCopy(ids.length, only?.name, only?.ownerId ? store.user(only.ownerId)?.name : undefined)
  const save = async () => {
    if (busy || !ids.length) return
    setBusy(true)
    try {
      await store.unassign(ids)
      onSaved?.()
      onClose()
    } catch {
      // The store already toasted the server's reason (above this dialog); the dialog stays open so it can be retried.
    } finally { setBusy(false) }
  }
  return <Dialog open onClose={() => { if (!busy) onClose() }} title={copy.title} width="sm"
    footer={<><Button variant="ghost" disabled={busy} onClick={onClose}>Cancel</Button><Button variant="primary" icon={<UserMinus className="h-4 w-4" />} disabled={busy || !ids.length} onClick={() => void save()}>{busy ? 'Working…' : copy.confirmLabel}</Button></>}>
    <div className="ui-form-stack">
      <div role="group" aria-label="What will happen" className="space-y-2 text-sm text-ink-600">
        {copy.lines.map(line => <p key={line}>{line}</p>)}
      </div>
    </div>
  </Dialog>
}
