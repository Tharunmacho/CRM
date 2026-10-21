import { useState } from 'react'
import { useStore } from '@/shared/state/crm-store'
import { Button, Field, Input, Select, Textarea } from '@/shared/ui/primitives'

export interface ReceiptDecision { decision: 'link' | 'ignore'; reason: string; candidate_id?: string }

export function ReceiptReview({ busy, submit }: { busy: boolean; submit: (body: ReceiptDecision) => Promise<boolean> }) {
  const store = useStore()
  const [open, setOpen] = useState(false)
  const [decision, setDecision] = useState<'link' | 'ignore'>('link')
  const [reason, setReason] = useState('')
  const [query, setQuery] = useState('')
  const [candidateId, setCandidateId] = useState('')
  const matches = store.candidates.filter((candidate) => !query.trim() || [candidate.name, candidate.ref, candidate.phone, candidate.passportNo].some((value) => value?.toLowerCase().includes(query.trim().toLowerCase()))).slice(0, 30)
  if (!open) return <Button size="sm" disabled={busy} onClick={() => setOpen(true)}>Review receipt</Button>
  return <div className="mt-3 w-full space-y-3 rounded-control border border-rule bg-white p-3">
    <Field label="Review decision"><Select value={decision} disabled={busy} onChange={(event) => setDecision(event.target.value as typeof decision)}><option value="link">Link evidence to an existing candidate</option><option value="ignore">Close without creating a candidate</option></Select></Field>
    {decision === 'link' && <>
      <p className="text-xs text-ink-600">Verify the source documents against the candidate. Original files and any selected document pages will be added to their history. Existing candidate details and ownership are retained.</p>
      <Input value={query} disabled={busy} onChange={(event) => setQuery(event.target.value)} placeholder="Find by name, phone, passport or reference" aria-label="Find existing candidate" />
      <Field label="Verified candidate" required><Select value={candidateId} disabled={busy} onChange={(event) => setCandidateId(event.target.value)}><option value="">Choose candidate</option>{matches.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.ref} · {candidate.name} · {candidate.phone}</option>)}{candidateId && !matches.some((candidate) => candidate.id === candidateId) && <option value={candidateId}>{store.candidate(candidateId)?.name}</option>}</Select></Field>
    </>}
    <Field label="Reason for this decision" required><Textarea value={reason} disabled={busy} onChange={(event) => setReason(event.target.value)} maxLength={2000} placeholder="Record what you checked and why this decision is appropriate" /></Field>
    <div className="flex flex-wrap gap-2"><Button size="sm" variant="primary" disabled={busy || !reason.trim() || (decision === 'link' && !candidateId)} onClick={async () => { if (await submit({ decision, reason: reason.trim(), ...(decision === 'link' ? { candidate_id: candidateId } : {}) })) setOpen(false) }}>Save review decision</Button><Button size="sm" disabled={busy} onClick={() => setOpen(false)}>Cancel</Button></div>
  </div>
}
