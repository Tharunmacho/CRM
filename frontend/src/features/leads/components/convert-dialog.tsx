import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, CheckCircle2, Link2, Lock, UserPlus } from 'lucide-react'
import { Dialog } from '@/shared/ui/overlay'
import { Button, Field, Input } from '@/shared/ui/primitives'
import { FileDrop } from '@/shared/ui/file-drop'
import { ApplicationFormButton } from '@/features/application-form/components/application-form-button'
import { convertLead, existingCandidateFrom, gateFromRefusal, leadApi, type ConvertResponse, type ExistingCandidate } from '../lead-api'
import { cleanPhone, dobLabel, type Lead } from '../lead-model'
import type { LeadGate } from '../lead-gate'

interface Props {
  lead: Lead | null
  onClose: () => void
  /** The lead or the candidate list changed: refresh the table. */
  onChanged: () => void
  blockedReason: string | null
  onGateRefusal: (gate: LeadGate) => void
}

type Fields = { name: string; phone: string; email: string; designation: string; country: string; city: string }

const fieldsOf = (lead: Lead): Fields => ({
  name: lead.name ?? '', phone: cleanPhone(lead.phone), email: lead.email ?? '', designation: lead.positionApplied ?? '', country: lead.countryInterest ?? '', city: lead.city ?? '',
})

/** Only what the person changed goes to the server as overrides; everything else comes from the lead. */
export function changedOverrides(lead: Lead, fields: Fields): Record<string, unknown> {
  const original = fieldsOf(lead)
  const out: Record<string, unknown> = {}
  if (fields.name.trim() !== original.name) out.name = fields.name.trim()
  if (fields.phone.trim() !== original.phone) out.phone = fields.phone.trim()
  if (fields.email.trim() !== original.email) out.email = fields.email.trim()
  if (fields.designation.trim() !== original.designation) out.designations = fields.designation.trim() ? [fields.designation.trim()] : []
  if (fields.country.trim() !== original.country) out.countryPreference = fields.country.trim()
  if (fields.city.trim() !== original.city) out.currentCity = fields.city.trim()
  return out
}

/**
 * Convert a lead to a Data Bank candidate: shows the candidate data that will be created, lets the recruiter correct it and
 * attach the updated resume, handles "this person is already a candidate" (open them, or link this lead) and ends with the
 * way into the new candidate and its application form.
 */
export function ConvertDialog({ lead, onClose, onChanged, blockedReason, onGateRefusal }: Props) {
  const navigate = useNavigate()
  const [fields, setFields] = useState<Fields | null>(null)
  const [resume, setResume] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [existing, setExisting] = useState<ExistingCandidate | null>(null)
  const [done, setDone] = useState<ConvertResponse | null>(null)
  const [linked, setLinked] = useState<ExistingCandidate | null>(null)

  useEffect(() => {
    if (!lead) return
    setFields(fieldsOf(lead)); setResume(null); setBusy(false); setError(''); setExisting(null); setDone(null); setLinked(null)
  }, [lead?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!lead || !fields) return null
  const set = (key: keyof Fields) => (event: React.ChangeEvent<HTMLInputElement>) => setFields((current) => (current ? { ...current, [key]: event.target.value } : current))
  const valid = fields.name.trim().length > 0 && (fields.phone.trim().length > 0 || fields.email.trim().length > 0)

  const submit = async () => {
    if (busy || blockedReason || !valid) return
    setBusy(true); setError(''); setExisting(null)
    try {
      const result = await convertLead(lead.id, resume, changedOverrides(lead, fields))
      setDone(result); onChanged()
    } catch (e) {
      const duplicate = existingCandidateFrom(e)
      if (duplicate) setExisting(duplicate)
      else {
        const gate = gateFromRefusal(e)
        if (gate) onGateRefusal(gate)
        setError(e instanceof Error ? e.message : 'The lead was not converted. Please try again.')
      }
    } finally { setBusy(false) }
  }

  const link = async () => {
    if (!existing || busy) return
    setBusy(true); setError('')
    try { await leadApi.linkCandidate(lead.id, existing.id); setLinked(existing); onChanged() } catch (e) {
      const gate = gateFromRefusal(e)
      if (gate) onGateRefusal(gate)
      setError(e instanceof Error ? e.message : 'We could not link the lead. Please try again.')
    } finally { setBusy(false) }
  }

  if (done || linked) {
    const candidate = done ? done.candidate : (linked as ExistingCandidate)
    return (
      <Dialog open onClose={onClose} title={done ? 'Candidate created' : 'Lead linked'} width="sm"
        footer={<>
          <Button variant="secondary" onClick={onClose}>Close</Button>
          <ApplicationFormButton candidateId={candidate.id} label="Open application form" />
          <Button variant="primary" onClick={() => { onClose(); navigate(`/candidates/${candidate.id}`) }}>Open candidate</Button>
        </>}>
        <div className="ld-done" role="status">
          <span className="ld-result-icon"><CheckCircle2 size={34} aria-hidden="true" /></span>
          <h3>{candidate.name}</h3>
          <p className="ld-done-ref">{candidate.ref}</p>
          <p>{done ? 'This lead is now a candidate in the Data Bank.' : 'This lead is now linked to the candidate who was already in the Data Bank.'}</p>
          {done && done.resumeAttached && <p className="ld-done-note">The resume was attached. It is being read, and the application form will fill in from it.</p>}
          {done && !done.resumeAttached && !done.resumeError && <p className="ld-done-note">No resume was attached yet. You can add it from the candidate's page.</p>}
          {done?.resumeError && <p className="ld-alert" role="alert"><AlertTriangle size={16} aria-hidden="true" /><span>The candidate was created, but the resume was not attached: {done.resumeError}</span></p>}
        </div>
      </Dialog>
    )
  }

  return (
    <Dialog open onClose={() => { if (!busy) onClose() }} title="Convert to candidate" width="lg"
      description="Check the details that will be saved in the Data Bank, attach the updated resume the person sent you, then convert."
      footer={<>
        <Button variant="secondary" disabled={busy} onClick={onClose}>Cancel</Button>
        <Button variant="primary" icon={<UserPlus className="h-4 w-4" />} disabled={busy || Boolean(blockedReason) || !valid} title={blockedReason ?? (!valid ? 'A name and a phone number or email are needed' : undefined)} onClick={() => void submit()}>
          {busy ? 'Converting…' : 'Convert to candidate'}
        </Button>
      </>}>
      <div className="ld-form">
        {blockedReason && <p className="ld-locked" role="alert"><Lock size={15} aria-hidden="true" />{blockedReason}</p>}

        {existing && (
          <div className="ld-dup" role="alert">
            <p className="ld-dup-title"><AlertTriangle size={16} aria-hidden="true" />This person is already a candidate</p>
            <p><strong>{existing.name}</strong> <span className="ident">{existing.ref}</span>{existing.phone ? ` · ${existing.phone}` : ''}{existing.ownerName ? ` · looked after by ${existing.ownerName}` : ''}. Same {(existing.matchedOn ?? ['phone or email']).join(' and ')}. Nothing was created.</p>
            <div className="ld-dup-actions">
              <Button variant="secondary" onClick={() => { onClose(); navigate(`/candidates/${existing.id}`) }}>Open that candidate</Button>
              <Button variant="primary" icon={<Link2 className="h-4 w-4" />} disabled={busy || Boolean(blockedReason)} title={blockedReason ?? undefined} onClick={() => void link()}>Link this lead</Button>
            </div>
          </div>
        )}

        <section className="ld-section">
          <h3>Candidate data</h3>
          <div className="ld-grid2">
            <Field label="Full name" required><Input value={fields.name} onChange={set('name')} /></Field>
            <Field label="Phone"><Input value={fields.phone} onChange={set('phone')} inputMode="tel" ident /></Field>
            <Field label="Email"><Input value={fields.email} onChange={set('email')} inputMode="email" /></Field>
            <Field label="Job"><Input value={fields.designation} onChange={set('designation')} /></Field>
            <Field label="Preferred country"><Input value={fields.country} onChange={set('country')} /></Field>
            <Field label="Current city"><Input value={fields.city} onChange={set('city')} /></Field>
          </div>
          <p className="ld-hint">Also carried over: {[lead.dob ? `born ${dobLabel(lead.dob)}` : '', lead.gender ?? '', lead.experienceTotalYears !== null && lead.experienceTotalYears !== undefined ? `${lead.experienceTotalYears} years of experience` : '', lead.campaignName ? `source: ${lead.campaignName}` : ''].filter(Boolean).join(' · ') || 'nothing else'}. {lead.ownerName ? `The candidate stays with ${lead.ownerName}.` : 'The candidate is not assigned. An admin can choose a recruiter later.'}</p>
        </section>

        <section className="ld-section">
          <h3>Updated resume</h3>
          {lead.hasResume && !resume && <p className="ld-hint">A resume is already attached to this lead and will be used. Choose a file below to use a different one.</p>}
          <FileDrop label="Updated resume" help="PDF, Word or a photo of the resume. It is read automatically after converting." optional variant="row" disabled={busy}
            onFile={(file) => { setResume(file) }} onChange={(value) => { if (!value) setResume(null) }} />
        </section>
        {error && <p role="alert" className="ld-alert"><AlertTriangle size={16} aria-hidden="true" /><span>{error}</span></p>}
      </div>
    </Dialog>
  )
}
