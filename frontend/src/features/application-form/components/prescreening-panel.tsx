import { ArrowRight, Save, ThumbsDown, UserX } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '@/shared/api/client'
import { cn } from '@/shared/lib/cn'
import { liveInterval } from '@/shared/state/live'
import { useStore } from '@/shared/state/crm-store'
import { Dialog } from '@/shared/ui/overlay'
import { Button, Field, Select, Textarea } from '@/shared/ui/primitives'
import { jobOptionText, jobUnavailableReason } from '../form-model'
import {
  forwardBlocker, jobChoices, prescreenBody, prescreenChip, prescreenFailure,
  type PrescreenAction, type PrescreenInfo,
} from '../prescreening'
import type { AssignmentClientOption } from '../types'
import '@/styles/prescreening-panel.css'

type Closing = 'decline' | 'reject'

/**
 * Prescreening (stage 1) for a candidate with no client or job: it is open to everyone without them. The recruiter writes feedback and can
 * save it, decline (the candidate said no), reject (not suitable) or move the candidate to the next stage; only that last action asks for
 * the client and the job (they are mandatory from stage 2). Every action goes through POST /candidates/{id}/prescreen, the server enforces
 * the same rule, and the Tasks lists follow at once through the live feed. `onChanged` lets the form re-read: after "Move to next stage"
 * the candidate has a placement and the stage window opens at stage 2.
 */
export function PrescreeningPanel({ candidateId, canWork, clients, onChanged, onState }: {
  candidateId: string
  canWork: boolean
  clients: AssignmentClientOption[]
  onChanged: () => void
  /** Told the state whenever it is read, so the stage list beside the panel shows the same thing (In progress / Failed). */
  onState?: (state: PrescreenInfo['state']) => void
}) {
  const store = useStore()
  const [info, setInfo] = useState<PrescreenInfo | null>(null)
  const [feedback, setFeedback] = useState('')
  const [clientId, setClientId] = useState('')
  const [orderId, setOrderId] = useState('')
  const [closing, setClosing] = useState<Closing | null>(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState<PrescreenAction | null>(null)
  const [error, setError] = useState('')
  const typed = useRef(false)   // the box holds words the person typed: a live read never overwrites them

  const load = useCallback(async () => {
    try {
      const next = await api.get<PrescreenInfo>(`/candidates/${encodeURIComponent(candidateId)}/prescreen`)
      setInfo(next)
      onState?.(next.state)
      if (!typed.current) setFeedback(next.feedback)
    } catch { /* the next live read tries again; the panel stays usable */ }
  }, [candidateId, onState])
  useEffect(() => { void load() }, [load])
  // Live: a colleague's decline or a Yes call shows here without a refresh.
  useEffect(() => liveInterval(load, 20_000, ['candidates', 'applications', 'tasks'], { debounceMs: 800 }), [load])

  const jobs = useMemo(() => jobChoices(clients, clientId, jobUnavailableReason), [clients, clientId])
  const blocker = forwardBlocker({ clientId, orderId, feedback })
  const state = info?.state ?? 'not_started'
  const chip = prescreenChip(state)
  const writable = canWork && (info?.canWork ?? true)
  const decided = state === 'declined' || state === 'rejected'

  const send = async (action: PrescreenAction, text: string) => {
    if (busy) return
    setBusy(action); setError('')
    try {
      const done = await api.post<{ notice?: string }>(`/candidates/${encodeURIComponent(candidateId)}/prescreen`, prescreenBody(action, text, orderId))
      if (!store.isCurrentSession()) return
      store.notify(done.notice || 'Saved.')
      typed.current = false
      setClosing(null); setReason('')
      await load()
      onChanged()
    } catch (cause) {
      if (!store.isCurrentSession()) return
      setError(prescreenFailure(cause instanceof Error ? cause.message : '', action))
      void load()
    } finally { setBusy(null) }
  }

  return (
    <div className="af-prescreen" id="af-stage-feedback" aria-labelledby="af-prescreen-title">
      <div className="af-prescreen-head">
        <div><small>Stage 1 of 13</small><h3 id="af-prescreen-title">Prescreening</h3></div>
        <span className={cn('af-prescreen-chip', chip.tone === 'busy' && 'is-busy', chip.tone === 'bad' && 'is-bad')} role="status">{chip.label}</span>
      </div>
      {error && <p className="af-prescreen-error" role="alert">{error}</p>}

      <div className="af-prescreen-box">
        <details open={Boolean(feedback) || undefined}>
          <summary>{feedback ? 'Feedback' : 'Add feedback (optional)'}</summary>
        <Field label="Feedback">
          <Textarea value={feedback} maxLength={10000} disabled={!writable || Boolean(busy)} placeholder="What did you learn from this candidate?"
            onChange={(event) => { typed.current = true; setFeedback(event.target.value) }} />
        </Field>
          <Button icon={<Save className="h-4 w-4" />} disabled={!writable || Boolean(busy) || !feedback.trim()} onClick={() => void send('save', feedback)}>
            {busy === 'save' ? 'Saving…' : 'Save feedback'}
          </Button>
        </details>
        <div className="af-prescreen-actions">
          <Button icon={<UserX className="h-4 w-4" />} disabled={!writable || Boolean(busy)} onClick={() => { setReason(feedback); setClosing('decline') }}>Not interested (decline)</Button>
          <Button icon={<ThumbsDown className="h-4 w-4" />} disabled={!writable || Boolean(busy)} onClick={() => { setReason(feedback); setClosing('reject') }}>Reject (not suitable)</Button>
        </div>
      </div>

      <div className="af-prescreen-forward">
        <h4>Move to next stage</h4>
        <div className="af-prescreen-pickers">
          <div>
            <label htmlFor="af-prescreen-client">Client</label>
            <Select id="af-prescreen-client" searchable value={clientId} aria-label="Client" disabled={!writable || Boolean(busy)}
              onChange={(event) => { setClientId(event.target.value); setOrderId('') }}>
              <option value="">Select a client</option>
              {clients.map((item) => <option key={item.id} value={item.id}>{item.companyName && item.companyName !== item.name ? `${item.name} · ${item.companyName}` : item.name}</option>)}
            </Select>
          </div>
          <div>
            <label htmlFor="af-prescreen-job">Job</label>
            <Select id="af-prescreen-job" searchable value={orderId} aria-label="Job" disabled={!writable || !clientId || Boolean(busy)} onChange={(event) => setOrderId(event.target.value)}>
              <option value="">{clientId ? jobs.length ? 'Select a job' : 'No jobs for this client' : 'Choose a client first'}</option>
              {jobs.map(({ job, reason: why }) => <option key={job.orderId} value={job.orderId} disabled={Boolean(why)}>{jobOptionText(job)}{why ? ` (${why})` : ''}</option>)}
            </Select>
          </div>
        </div>
        <div className="af-prescreen-actions">
          <Button variant="primary" icon={<ArrowRight className="h-4 w-4" />} disabled={!writable || Boolean(busy) || Boolean(blocker) || decided}
            onClick={() => void send('forward', feedback)}>{busy === 'forward' ? 'Moving…' : 'Move to next stage'}</Button>
          {writable && blocker && !decided && <p className="af-prescreen-why" role="status">{blocker}</p>}
          {decided && <p className="af-prescreen-why" role="status">Record a Yes call first to bring this candidate back.</p>}
        </div>
      </div>

      <Dialog open={closing !== null} onClose={() => { if (!busy) setClosing(null) }} width="sm"
        title={closing === 'reject' ? 'Reject this candidate?' : 'Mark as not interested?'}
        description={closing === 'reject'
          ? 'The candidate is not suitable. They move to the Rejected tab of the Tasks page. No client or job is needed.'
          : 'The candidate said no. They move to the Declined tab of the Tasks page. No client or job is needed.'}
        footer={<>
          <Button disabled={Boolean(busy)} onClick={() => setClosing(null)}>Cancel</Button>
          <Button variant="primary" disabled={Boolean(busy)} onClick={() => closing && void send(closing, reason)}>
            {busy === 'decline' || busy === 'reject' ? 'Saving…' : closing === 'reject' ? 'Reject candidate' : 'Mark not interested'}
          </Button>
        </>}>
        <Field label="Reason (optional)">
          <Textarea value={reason} maxLength={10000} disabled={Boolean(busy)} placeholder="Why?" onChange={(event) => setReason(event.target.value)} />
        </Field>
      </Dialog>
    </div>
  )
}
