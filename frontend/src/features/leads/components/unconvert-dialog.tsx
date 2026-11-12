import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, Loader2, Undo2 } from 'lucide-react'
import { Dialog } from '@/shared/ui/overlay'
import { Button, Field, Textarea } from '@/shared/ui/primitives'
import { checkLeadUnconversion, unconvertLead, type UnconvertCheck, type UnconvertResponse } from '../lead-api'
import { leadStatus, type Lead } from '../lead-model'

/** Check the exact linked candidate before offering an explicit reversal. The server repeats all guards when saving. */
export function UnconvertDialog({ lead, onClose, onSaved }: {
  lead: Lead | null
  onClose: () => void
  onSaved: (result: UnconvertResponse) => void
}) {
  const [check, setCheck] = useState<UnconvertCheck | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [reason, setReason] = useState('')
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const submitting = useRef(false)

  useEffect(() => {
    setReason(''); setBusy(false); submitting.current = false
  }, [lead?.id])

  useEffect(() => {
    if (!lead) { setCheck(null); return }
    const controller = new AbortController()
    setCheck(null); setLoading(true); setError('')
    checkLeadUnconversion(lead.id, controller.signal).then((result) => {
      if (!controller.signal.aborted) setCheck(result)
    }).catch((cause) => {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'The linked candidate could not be checked. Try again.')
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [lead?.id, reload])

  if (!lead) return null
  const candidate = check?.candidate
  const ready = check?.allowed === true && Boolean(candidate?.id) && reason.trim().length >= 3
  const submit = async () => {
    if (!ready || !candidate || submitting.current) return
    submitting.current = true; setBusy(true); setError('')
    let result: UnconvertResponse
    try { result = await unconvertLead(lead.id, candidate.id, reason.trim()) }
    catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The move could not be confirmed. Try again with the same reason.')
      submitting.current = false; setBusy(false)
      return
    }
    // A later list refresh must never turn this successful mutation into a retry prompt.
    onSaved(result)
  }
  const close = () => { if (!submitting.current) onClose() }

  return (
    <Dialog open onClose={close} title="Move back to Lead" description={lead.name} width="md"
      footer={<><Button disabled={busy} onClick={close}>Cancel</Button><Button variant="danger" icon={busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Undo2 className="h-4 w-4" />} disabled={loading || busy || !ready} onClick={() => void submit()}>{busy ? 'Moving…' : 'Move back to Lead'}</Button></>}>
      <div className="ld-form">
        {loading && <p className="ld-hint" role="status">Checking the linked candidate and saved work…</p>}
        {check?.allowed && candidate && <>
          <div className="ld-confirm-lines" role="group" aria-label="What will happen">
            <p><b>{candidate.name}</b> <span className="ident">{candidate.ref}</span> will be removed from the Data Bank.</p>
            <p>The lead, its files and its history will remain. Its status will return to <b>{leadStatus(check.restoredStatus).label}</b>.</p>
            <p>Use this to correct a conversion made by mistake.</p>
          </div>
          <Field label="Reason" hint="Saved in the lead history. At least 3 characters."><Textarea rows={3} maxLength={1000} value={reason} disabled={busy} onChange={(event) => setReason(event.target.value)} placeholder="Why are you moving this candidate back to a lead?" /></Field>
        </>}
        {check && !check.allowed && <p className="ld-alert" role="alert"><AlertTriangle size={16} aria-hidden="true" /><span>{check.reason || 'This candidate cannot be moved back to a lead.'}</span></p>}
        {error && <p className="ld-alert" role="alert"><AlertTriangle size={16} aria-hidden="true" /><span>{error}</span></p>}
        {!loading && !busy && (error || (check && !check.allowed)) && <Button onClick={() => setReload((value) => value + 1)}>Check again</Button>}
      </div>
    </Dialog>
  )
}
