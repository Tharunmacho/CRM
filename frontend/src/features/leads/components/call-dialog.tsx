import { useEffect, useState } from 'react'
import { Lock, PhoneCall } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { Dialog } from '@/shared/ui/overlay'
import { Button, Field, Input, Textarea } from '@/shared/ui/primitives'
import { leadApi } from '../lead-api'
import { CALL_OUTCOMES, cleanPhone, fromLocalInput, leadStatus, statusAfterCall, toLocalInput, type Lead } from '../lead-model'
import { gateFromRefusal } from '../lead-api'
import type { LeadGate } from '../lead-gate'

interface Props {
  lead: Lead | null
  onClose: () => void
  onSaved: (lead: Lead) => void
  /** Why work is blocked right now (the daily-work gate), or null. */
  blockedReason: string | null
  onGateRefusal: (gate: LeadGate) => void
}

const addDays = (days: number) => { const date = new Date(); date.setDate(date.getDate() + days); date.setHours(10, 0, 0, 0); return toLocalInput(date.toISOString()) }

/** Add remarks: what happened, a note, and when to call again. The server moves the status (shown before saving). */
export function CallDialog({ lead, onClose, onSaved, blockedReason, onGateRefusal }: Props) {
  const [outcome, setOutcome] = useState('answered')
  const [remarks, setRemarks] = useState('')
  const [follow, setFollow] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!lead) return
    setOutcome('answered'); setRemarks(''); setFollow(toLocalInput(lead.nextFollowUpAt)); setError(''); setBusy(false)
  }, [lead?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!lead) return null
  const after = statusAfterCall(outcome, lead.status)
  const closes = outcome === 'not_interested' || outcome === 'wrong_number'

  const save = async () => {
    if (busy) return
    setBusy(true); setError('')
    try {
      const saved = await leadApi.logCall(lead.id, { outcome, remarks: remarks.trim(), nextFollowUpAt: closes ? null : fromLocalInput(follow) })
      onSaved(saved)
    } catch (e) {
      const gate = gateFromRefusal(e)
      if (gate) onGateRefusal(gate)
      setError(e instanceof Error ? e.message : 'The remarks were not saved. Please try again.')
    } finally { setBusy(false) }
  }

  return (
    <Dialog open onClose={onClose} title="Add remarks" width="md"
      description={`${lead.name || 'Lead'}${lead.phone ? ` · ${cleanPhone(lead.phone)}` : ''}`}
      footer={<>
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button variant="primary" disabled={busy || Boolean(blockedReason)} title={blockedReason ?? undefined} icon={<PhoneCall className="h-4 w-4" />} onClick={() => void save()}>{busy ? 'Saving…' : 'Save remarks'}</Button>
      </>}>
      <div className="ld-form">
        {blockedReason && <p className="ld-locked" role="alert"><Lock size={15} aria-hidden="true" />{blockedReason}</p>}
        <fieldset className="ld-outcomes">
          <legend>What happened?</legend>
          <div role="radiogroup" aria-label="Call outcome">
            {CALL_OUTCOMES.map((item) => (
              <button key={item.value} type="button" role="radio" aria-checked={outcome === item.value} className={cn('ld-outcome', outcome === item.value && 'is-on')} onClick={() => setOutcome(item.value)}>{item.label}</button>
            ))}
          </div>
          <p className="ld-hint">{after && after !== lead.status ? <>Status will change from <b>{leadStatus(lead.status).label}</b> to <b>{leadStatus(after).label}</b>.</> : <>Status stays <b>{leadStatus(lead.status).label}</b>.</>}</p>
        </fieldset>
        <Field label="Remarks" hint="What did they say? Whatever you write is saved in the lead's timeline.">
          <Textarea value={remarks} maxLength={2000} rows={3} placeholder="e.g. Will send the updated resume tomorrow" onChange={(event) => setRemarks(event.target.value)} />
        </Field>
        {!closes && (
          <Field label="Next follow-up" hint="Leave empty if there is nothing to follow up.">
            <Input type="datetime-local" value={follow} onChange={(event) => setFollow(event.target.value)} />
            <span className="ld-quick" role="group" aria-label="Quick follow-up dates">
              <button type="button" onClick={() => setFollow(addDays(1))}>Tomorrow</button>
              <button type="button" onClick={() => setFollow(addDays(3))}>In 3 days</button>
              <button type="button" onClick={() => setFollow(addDays(7))}>Next week</button>
              {follow && <button type="button" onClick={() => setFollow('')}>Clear</button>}
            </span>
          </Field>
        )}
        {error && <p role="alert" className="ld-alert"><span>{error}</span></p>}
      </div>
    </Dialog>
  )
}
