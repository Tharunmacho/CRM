import { RotateCcw } from 'lucide-react'
import { useState } from 'react'
import { api } from '@/shared/api/client'
import type { Application } from '@/shared/model/types'
import { useStore } from '@/shared/state/crm-store'
import { Dialog } from '@/shared/ui/overlay'
import { Button, Field, Textarea } from '@/shared/ui/primitives'
import { MIN_REOPEN_REASON, reopenExplanation, reopenFailure, reopenReasonOk } from '../reopen-application'

/**
 * "Reopen application" for a closed placement: a button that opens a small dialog asking for a reason, then
 * POST /applications/{id}/reopen-application. The server puts the candidate back at the stage where it ended with an open task, so
 * `onRecorded` lets the Tasks page re-read at once (the candidate leaves Declined / Rejected) and `onDone` lets the host screen reload.
 */
export function ReopenApplication({ app, stageTitle, disabled = false, onRecorded, onDone }: {
  app: Application; stageTitle: string; disabled?: boolean; onRecorded?: () => void; onDone?: () => void
}) {
  const store = useStore()
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const close = () => { if (!busy) { setOpen(false); setError('') } }
  const submit = async () => {
    if (busy || disabled || !reopenReasonOk(reason)) return
    setBusy(true); setError('')
    try {
      await api.post(`/applications/${app.id}/reopen-application`, { reason: reason.trim(), lock_version: app.lockVersion })
      if (!store.isCurrentSession()) return
      onRecorded?.()
      store.notify(`${stageTitle} reopened.`)
      setOpen(false); setReason('')
      try { await store.refresh({ keepCurrent: true }) } catch { /* the live refresh catches up */ }
      onDone?.()
    } catch (cause) {
      if (!store.isCurrentSession()) return
      setError(reopenFailure(cause instanceof Error ? cause.message : ''))
      void store.refresh({ keepCurrent: true }).catch(() => undefined)   // the next try holds the latest version
    } finally { setBusy(false) }
  }
  return <>
    <Button variant="primary" size="sm" disabled={disabled} icon={<RotateCcw className="h-4 w-4" />} onClick={() => setOpen(true)}>Reopen stage</Button>
    <Dialog open={open} onClose={close} title={`Reopen ${stageTitle}?`} description={reopenExplanation(stageTitle)} width="sm"
      footer={<>
        <Button disabled={busy} onClick={close}>Cancel</Button>
        <Button variant="primary" disabled={busy || disabled || !reopenReasonOk(reason)} onClick={() => void submit()}>{busy ? 'Reopening…' : 'Reopen stage'}</Button>
      </>}>
      {error && <p role="alert" className="mb-3 text-sm text-stamp-700">{error}</p>}
      <Field label="Reason for reopening" required hint={`At least ${MIN_REOPEN_REASON} characters.`}>
        <Textarea value={reason} onChange={(event) => setReason(event.target.value)} maxLength={500} disabled={busy} placeholder="Why does this candidate continue?" />
      </Field>
    </Dialog>
  </>
}
