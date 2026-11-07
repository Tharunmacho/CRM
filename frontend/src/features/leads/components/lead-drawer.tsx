import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, ExternalLink, FileUp, Link2, Lock, MessageCircle, PhoneCall, Trash2, UserCheck, UserPlus, Loader2 } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import type { User } from '@/shared/model/types'
import { Sheet } from '@/shared/ui/overlay'
import { Button, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { FileLink } from '@/shared/ui/file-drop'
import { gateFromRefusal, leadApi, type LeadActivity, type LeadDetail } from '../lead-api'
import {
  PLATFORM_LABEL, SETTABLE_STATUSES, cleanPhone, createdParts, dobLabel, followUpLabel, followUpState, fromLocalInput, leadStatus, platformKind, toLocalInput, whatsappLink, yearsLabel,
  type Lead, type LeadActor,
} from '../lead-model'
import { GATE_REASON, gateBlocks, type GateState, type LeadGate } from '../lead-gate'
import { CopyButton } from './lead-columns'
import { openWhatsApp } from './wa-template'

interface Props {
  /** The row that was clicked; shown at once while the full detail loads. */
  lead: Lead | null
  me: LeadActor
  users: User[]
  gate: GateState
  /** Bumps when the list reloaded (a live change), so the open lead is read again. */
  version: unknown
  workRefusal: string | null
  canAssign: boolean
  onClose: () => void
  onChanged: () => void
  onLogCall: (lead: Lead) => void
  onConvert: (lead: Lead) => void
  onAssign: (lead: Lead) => void
  onUploadResume: (lead: Lead, file: File) => Promise<void>
  onGateRefusal: (gate: LeadGate) => void
  /** Admin only: delete this lead (asks for confirmation first). */
  canDelete?: boolean
  onDelete?: (lead: Lead) => void
  /** The status was saved from here: the screen offers an Undo toast. */
  onStatusChange?: (lead: Lead, from: string, to: string) => void
}

const Row = ({ label, children }: { label: string; children: ReactNode }) => (
  <div className="ld-dl-row"><dt>{label}</dt><dd>{children || <span className="db-muted">–</span>}</dd></div>
)
const Section = ({ title, children }: { title: string; children: ReactNode }) => (
  <section className="ld-dsec"><h3>{title}</h3><dl className="ld-dl">{children}</dl></section>
)

/** The person's own wording when it says more than a number ("10+ years"), else the number as years. */
const experienceText = (text: string | null | undefined, years: number | string | null | undefined) =>
  text && !/^\d+(\.\d+)?$/.test(text.trim()) ? text : yearsLabel(years) ?? text ?? ''

const KIND_LABEL: Record<string, string> = { call: 'Call', note: 'Note', status: 'Status', assign: 'Assigned', import: 'Imported', convert: 'Converted', resume: 'Resume', edit: 'Edited' }

function Timeline({ items }: { items: LeadActivity[] }) {
  if (items.length === 0) return <p className="ld-hint">Nothing has happened with this lead yet.</p>
  return (
    <ol className="ld-timeline">
      {items.map((item) => {
        const parts = createdParts(item.createdAt)
        return (
          <li key={item.id} className={cn('ld-tl', `is-${item.kind}`)}>
            <span className="ld-tl-dot" aria-hidden="true" />
            <div className="ld-tl-body">
              <p><b>{KIND_LABEL[item.kind] ?? item.kind}</b>{item.userName ? ` · ${item.userName}` : ''}<time dateTime={item.createdAt}>{parts ? `${parts.day} ${parts.year}, ` : ''}{new Date(item.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time></p>
              {item.summary && <p className="ld-tl-text">{item.summary}</p>}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

/** The side drawer of one lead: everything we know, what to do next, and the timeline. */
export function LeadDrawer({ lead: row, me, users, gate, version, workRefusal, canAssign, onClose, onChanged, onLogCall, onConvert, onAssign, onUploadResume, onGateRefusal, canDelete, onDelete, onStatusChange }: Props) {
  const navigate = useNavigate()
  const [detail, setDetail] = useState<LeadDetail | null>(null)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [notes, setNotes] = useState('')
  const [follow, setFollow] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [uploading, setUploading] = useState(false)
  const id = row?.id

  const load = useCallback(async (signal?: AbortSignal) => {
    if (!id) return
    try {
      const data = await leadApi.detail(id, signal)
      if (signal?.aborted) return
      setDetail(data); setError('')
    } catch (e) { if (!signal?.aborted) setError(e instanceof Error ? e.message : 'We could not load this lead.') }
  }, [id])

  useEffect(() => { setDetail(null); setError(''); setSaveError('') }, [id])
  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal)
    return () => controller.abort()
  }, [load, version])
  const shown: Lead | null = detail ?? row
  // Fill the edit fields when a different lead (or a fresh copy from the server) arrives.
  const stamp = detail ? `${detail.id}:${detail.status}:${detail.notes ?? ''}:${detail.nextFollowUpAt ?? ''}` : ''
  useEffect(() => {
    if (!detail) return
    setStatus(detail.status); setNotes(detail.notes ?? ''); setFollow(toLocalInput(detail.nextFollowUpAt))
  }, [stamp]) // eslint-disable-line react-hooks/exhaustive-deps

  const blocked = gateBlocks(gate) ? GATE_REASON : workRefusal
  const dirty = useMemo(() => !!detail && (status !== detail.status || notes.trim() !== (detail.notes ?? '').trim() || follow !== toLocalInput(detail.nextFollowUpAt)), [detail, status, notes, follow])

  if (!row || !shown) return null
  const phone = cleanPhone(shown.phone)
  const wa = whatsappLink(phone)
  const st = leadStatus(shown.status)
  const created = createdParts(shown.createdTime)
  const followState = followUpState(shown.nextFollowUpAt, shown.status)
  const platform = platformKind(shown.platform)
  const extra = Object.entries(detail?.extra ?? {}).filter(([, value]) => value && String(value).trim())

  const save = async () => {
    if (!detail || !dirty || saving) return
    setSaving(true); setSaveError('')
    const body: Record<string, unknown> = {}
    if (status !== detail.status) body.status = status
    if (notes.trim() !== (detail.notes ?? '').trim()) body.notes = notes.trim()
    if (follow !== toLocalInput(detail.nextFollowUpAt)) body.nextFollowUpAt = fromLocalInput(follow)
    try { await leadApi.update(detail.id, body); await load(); onChanged(); if (body.status !== undefined) onStatusChange?.(detail, detail.status, String(body.status)) } catch (e) {
      const refused = gateFromRefusal(e)
      if (refused) onGateRefusal(refused)
      setSaveError(e instanceof Error ? e.message : 'Your changes were not saved. Please try again.')
    } finally { setSaving(false) }
  }

  const attach = async (file: File) => {
    setUploading(true); setSaveError('')
    try { await onUploadResume(shown, file); await load() } catch (e) { setSaveError(e instanceof Error ? e.message : 'The resume was not uploaded.') } finally { setUploading(false) }
  }

  return (
    <Sheet open onClose={onClose} title={shown.name || 'Lead'} width="lg"
      description={[phone, shown.positionApplied].filter(Boolean).join(' · ') || undefined}>
      <div className="ld-drawer">
        <div className="ld-d-top">
          <span className={cn('db-tone', `is-${st.tone}`, 'ld-d-status')}>{st.label}</span>
          {shown.candidateId && <button type="button" className="ld-cand" onClick={() => navigate(`/candidates/${shown.candidateId}`)}><span>Candidate</span><b>{shown.candidateRef || 'open'}</b></button>}
          {followState !== 'none' && followState !== 'closed' && <span className={cn('ld-d-follow', followState === 'overdue' && 'is-late')}>Follow up: {followUpLabel(shown.nextFollowUpAt)}</span>}
        </div>

        {blocked && <p className="ld-locked" role="alert"><Lock size={15} aria-hidden="true" />{blocked}</p>}
        {error && <p className="ld-alert" role="alert"><AlertTriangle size={16} aria-hidden="true" /><span>{error}</span></p>}

        {shown.matchedCandidateId && !shown.candidateId && (
          <div className="ld-dup is-soft">
            <p className="ld-dup-title"><Link2 size={16} aria-hidden="true" />Already in the Data Bank</p>
            <p>{shown.matchedCandidateName || 'A candidate'} {shown.matchedCandidateRef ? <span className="ident">{shown.matchedCandidateRef}</span> : null} has the same phone or email. Open them, or convert this lead to link it.</p>
            <div className="ld-dup-actions"><Button variant="secondary" icon={<ExternalLink className="h-4 w-4" />} onClick={() => navigate(`/candidates/${shown.matchedCandidateId}`)}>Open that candidate</Button></div>
          </div>
        )}

        <div className="ld-d-actions">
          <Button variant="primary" icon={<PhoneCall className="h-4 w-4" />} disabled={Boolean(blocked)} title={blocked ?? undefined} onClick={() => onLogCall(shown)}>Log a call</Button>
          <Button variant="secondary" icon={<UserPlus className="h-4 w-4" />} disabled={Boolean(blocked) || Boolean(shown.candidateId)} title={shown.candidateId ? 'This lead is already a candidate.' : blocked ?? undefined} onClick={() => onConvert(shown)}>Convert to candidate</Button>
          <label className={cn('ld-btn ld-file-btn', (blocked || uploading || shown.candidateId) && 'is-disabled')} title={blocked ?? (shown.candidateId ? 'This lead is already a candidate.' : 'Attach the updated resume')}>
            {uploading ? <Loader2 className="ld-spin h-4 w-4" /> : <FileUp className="h-4 w-4" />}{shown.hasResume ? 'Replace resume' : 'Upload resume'}
            <input type="file" className="sr-only" accept=".pdf,.doc,.docx,.png,.jpg,.jpeg,.webp" disabled={Boolean(blocked) || uploading || Boolean(shown.candidateId)}
              onChange={(event) => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ''; if (file) void attach(file) }} />
          </label>
          {canAssign && <Button variant="secondary" icon={<UserCheck className="h-4 w-4" />} onClick={() => onAssign(shown)}>Assign</Button>}
          {canDelete && onDelete && <Button variant="dangerGhost" icon={<Trash2 className="h-4 w-4" />} disabled={Boolean(shown.candidateId)} title={shown.candidateId ? 'This lead was converted to a candidate, so it cannot be deleted.' : undefined} onClick={() => onDelete(shown)}>Delete lead</Button>}
          {wa && <button type="button" className="ld-btn ld-wa-btn" aria-haspopup="dialog" onClick={(event) => openWhatsApp({ id: shown.id, name: shown.name, phone, positionApplied: shown.positionApplied }, event.currentTarget)}><MessageCircle className="h-4 w-4" />WhatsApp</button>}
        </div>

        {detail?.resume && (
          <p className="ld-resume-line">Resume on file: <FileLink url={detail.resume.url}>{detail.resume.name}</FileLink> <span className="db-muted">· {detail.resume.sizeKb} KB · version {detail.resume.version}</span></p>
        )}

        <section className="ld-dsec ld-edit">
          <h3>Status, follow-up and notes</h3>
          <div className="ld-grid2">
            <Field label="Status">
              <Select aria-label="Status" value={status || shown.status} disabled={Boolean(blocked) || !detail || Boolean(shown.candidateId)} onChange={(event) => setStatus(event.target.value)}>
                {SETTABLE_STATUSES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                {shown.status === 'converted' && <option value="converted">Converted</option>}
              </Select>
            </Field>
            <Field label="Next follow-up">
              <Input type="datetime-local" value={follow} disabled={Boolean(blocked) || !detail} onChange={(event) => setFollow(event.target.value)} />
            </Field>
          </div>
          <Field label="Notes">
            <Textarea rows={3} value={notes} maxLength={4000} disabled={Boolean(blocked) || !detail} placeholder="Anything worth remembering about this person" onChange={(event) => setNotes(event.target.value)} />
          </Field>
          {saveError && <p role="alert" className="ld-alert"><span>{saveError}</span></p>}
          <div className="ld-edit-foot"><Button variant="primary" disabled={!dirty || saving || Boolean(blocked)} title={blocked ?? undefined} onClick={() => void save()}>{saving ? 'Saving…' : 'Save changes'}</Button></div>
        </section>

        <Section title="Contact">
          <Row label="Phone">{phone ? <span className="ld-copyrow"><span className="ident">{phone}</span><CopyButton value={phone} label="Copy phone" /></span> : null}</Row>
          <Row label="Email">{shown.email ? <span className="ld-copyrow"><span>{shown.email}</span><CopyButton value={shown.email} label="Copy email" /></span> : null}</Row>
          <Row label="City / State">{[shown.city, shown.state].filter(Boolean).join(', ')}</Row>
          <Row label="Assigned to">{shown.ownerName ?? users.find((user) => user.id === shown.ownerId)?.name ?? (shown.ownerId ? '' : 'Nobody yet')}</Row>
          <Row label="Calls">{`${shown.callCount ?? 0}${shown.lastContactedAt ? ` · last ${new Date(shown.lastContactedAt).toLocaleDateString()}` : ''}`}</Row>
        </Section>

        <Section title="About the person">
          <Row label="Job applied">{shown.positionApplied}</Row>
          <Row label="Country interest">{shown.countryInterest}</Row>
          <Row label="Restaurant experience">{experienceText(shown.experienceRestaurantText, shown.experienceRestaurantYears)}</Row>
          <Row label="Total experience">{experienceText(shown.experienceTotalText, shown.experienceTotalYears)}</Row>
          <Row label="Gender">{shown.gender}</Row>
          <Row label="Date of birth">{dobLabel(shown.dob)}</Row>
          <Row label="Marital status">{shown.maritalStatus}</Row>
          <Row label="Education">{shown.educationLevel}</Row>
        </Section>

        <Section title="Ad and campaign">
          <Row label="Created">{created ? `${created.day} ${created.year}, ${new Date(shown.createdTime as string).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : null}</Row>
          <Row label="Platform">{shown.platform ? `${PLATFORM_LABEL[platform]}${shown.isOrganic ? ' (organic)' : ''}` : null}</Row>
          <Row label="Campaign">{shown.campaignName}</Row>
          <Row label="Ad set">{shown.adsetName}</Row>
          <Row label="Ad">{shown.adName}</Row>
          <Row label="Form">{shown.formName}</Row>
          <Row label="Lead ID"><span className="ident">{shown.externalId}</span></Row>
        </Section>

        {extra.length > 0 && (
          <Section title="Answers from the form">
            {extra.map(([key, value]) => <Row key={key} label={key.replace(/_/g, ' ')}>{String(value)}</Row>)}
          </Section>
        )}

        <section className="ld-dsec">
          <h3>Timeline</h3>
          {detail ? <Timeline items={detail.activities} /> : <p className="ld-hint" role="status">Loading…</p>}
        </section>
      </div>
    </Sheet>
  )
}
