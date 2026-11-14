import { useRef, useState, type ReactNode } from 'react'
import { Check, CheckCircle2, Copy, FileUp, Instagram, Facebook, UserCheck, Globe, Link2, BellRing, Info } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { cn } from '@/shared/lib/cn'
import { Dialog } from '@/shared/ui/overlay'
import { whatsappHref } from '@/features/candidates/data-bank-view'
import { WhatsAppTemplateButton } from './wa-template'
import { Avatar, RowMenu } from '@/features/candidates/components/db-parts'
import {
  cleanPhone, createdParts, dobLabel, experienceAnswer, experienceQuestionSummary, extraAnswer, followUpLabel, followUpState, isClosed, leadAge, leadDate, leadStatus,
  platformKind, PLATFORM_LABEL, type ExtraColumn, type Lead,
} from '../lead-model'
import { leadMenuItems, workBlock, type LeadColumnContext } from './lead-actions'
import { EMPTY_VALUE, leadFieldRows, type LeadFieldDef } from '../lead-fields'

const Dash = () => <span className="db-muted" aria-label="None">–</span>

export function CreatedCell({ lead }: { lead: Lead }) {
  const date = leadDate(lead)
  const parts = createdParts(date?.value)
  if (!date || !parts) return <Dash />
  return <span className="db-2l" title={`${date.label} ${new Date(date.value).toLocaleString()}`}><strong className="db-num">{parts.day}</strong><small>{parts.year}{date.label === 'Imported' && ' · Imported'}</small></span>
}

export function NameCell({ lead }: { lead: Lead }) {
  const dob = dobLabel(lead.dob)
  const age = leadAge(lead.dob)
  const phone = cleanPhone(lead.phone)
  return (
    <span className="db-2l is-name ld-identity" title={`${lead.name}${dob ? ` · born ${dob}` : ''}${lead.gender ? ` · ${lead.gender}` : ''}`}>
      <strong className="ld-identity-name">{lead.name || 'Unnamed'}</strong>
      <small>{age === null ? 'Age —' : `Age ${age}`}</small>
      {phone ? <span className="ld-identity-phone"><span className="ident db-num">{phone}</span>{whatsappHref(phone) && <LeadWhatsApp lead={lead} />}<CopyButton value={phone} label={`Copy ${phone}`} /></span> : <small className="db-muted">Phone —</small>}
    </span>
  )
}

/** Copy to clipboard with a brief tick; never throws when the browser refuses. */
export function CopyButton({ value, label }: { value: string; label: string }) {
  const [done, setDone] = useState(false)
  return (
    <button type="button" className="ld-copy" title={done ? 'Copied' : label} aria-label={label}
      onClick={(event) => {
        event.stopPropagation()
        void navigator.clipboard?.writeText(value).then(() => { setDone(true); window.setTimeout(() => setDone(false), 1200) }).catch(() => {})
      }}>
      {done ? <Check size={12} aria-hidden="true" /> : <Copy size={12} aria-hidden="true" />}
    </button>
  )
}

/** Phone, the green WhatsApp glyph and a copy button on one line; the e-mail under it (cut with an ellipsis, whole in the tooltip). */
export function ContactCell({ lead }: { lead: Lead }) {
  const phone = cleanPhone(lead.phone)
  if (!phone && !lead.email) return <Dash />
  return (
    <span className="db-2l ld-contact" title={[phone, lead.email].filter(Boolean).join(' · ')}>
      {phone
        ? <span className="ld-line"><span className="ident db-num">{phone}</span>{whatsappHref(phone) && <LeadWhatsApp lead={lead} />}<CopyButton value={phone} label={`Copy ${phone}`} /></span>
        : <span className="db-muted">No phone</span>}
      {lead.email && <small className="db-ellip" title={lead.email}>{lead.email}</small>}
    </span>
  )
}

/** A readable job title using the same plain text treatment as the other lead facts. */
export function PositionText({ value }: { value?: string | null }) {
  const text = (value ?? '').trim()
  if (!text) return <Dash />
  return <strong className="ld-position" title={text}>{text}</strong>
}

export function ExperienceCell({ lead, kind = 'total' }: { lead: Lead; kind?: 'restaurant' | 'india' | 'abroad' | 'total' }) {
  const [open, setOpen] = useState(false)
  const { value, question } = experienceAnswer(lead, kind)
  if (!value) return <Dash />
  const label = { restaurant: 'Restaurant experience', india: 'India experience', abroad: 'Abroad experience', total: 'Total experience' }[kind]
  const showQuestion = Boolean(question && /^(yes|no)$/i.test(value))
  const title = `${question || label}: ${value}`
  const content = <>
    <strong className="db-num ld-experience-value">{value}{showQuestion && <Info size={12} aria-hidden="true" />}</strong>
    {showQuestion && <small>{experienceQuestionSummary(question)}</small>}
  </>
  return (
    <span className={`ld-experience-cell is-${kind}`} onClick={showQuestion ? event => event.stopPropagation() : undefined}>
      {showQuestion
        ? <button type="button" className="db-2l ld-experience-answer" title={title} aria-label={`${title}. View experience question`} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}>{content}</button>
        : <span className="db-2l" title={title}>{content}</span>}
      <Dialog open={open} onClose={() => setOpen(false)} title="Experience answers" width="sm">
        <dl className="ld-experience-questions">
          <div><dt>{question || label}</dt><dd>{value}</dd></div>
        </dl>
      </Dialog>
    </span>
  )
}

export function EducationCell({ lead }: { lead: Lead }) {
  const value = lead.educationLevel?.trim()
  return value ? <span className="ld-answer-text" title={value}>{value}</span> : <Dash />
}

export function StateCell({ lead }: { lead: Lead }) {
  const value = lead.state?.trim()
  return value ? <span className="ld-answer-text" title={value}>{value}</span> : <Dash />
}

export function LocationCell({ lead }: { lead: Lead }) {
  if (!lead.city && !lead.state) return <Dash />
  return (
    <span className="db-2l ld-location" title={[lead.city, lead.state].filter(Boolean).join(', ')}>
      <strong className="ld-answer-text">{lead.city || lead.state}</strong>
      {lead.city && lead.state && <small>{lead.state}</small>}
    </span>
  )
}

export function PlatformIcon({ platform }: { platform?: string | null }) {
  const kind = platformKind(platform)
  const Icon = kind === 'facebook' ? Facebook : kind === 'instagram' ? Instagram : kind === 'google' ? Globe : Link2
  return <span className={cn('ld-platform', `is-${kind}`)} title={platform ? `${PLATFORM_LABEL[kind]} (${platform})` : 'Platform not given'}><Icon size={13} aria-hidden="true" /><span className="sr-only">{PLATFORM_LABEL[kind]}</span></span>
}

export function CampaignCell({ lead }: { lead: Lead }) {
  if (!lead.campaignName && !lead.adName) return <span className="ld-line"><PlatformIcon platform={lead.platform} /><Dash /></span>
  return (
    <span className="ld-camp" title={[lead.campaignName, lead.adName, lead.formName].filter(Boolean).join(' · ')}>
      <PlatformIcon platform={lead.platform} />
      <span className="ld-camp-text">
        <strong className="db-ellip ld-camp-name">{lead.campaignName || lead.adName}</strong>
        {lead.formName && <small className="db-ellip">{lead.formName}</small>}
      </span>
    </span>
  )
}

export function StatusText({ lead }: { lead: Lead }) {
  const status = leadStatus(lead.status)
  return <span className={cn('db-tone', `is-${status.tone}`)} title={status.label}>{status.label}</span>
}

/** Under the recruiter's name: once the resume is in the lead is clearly interested, and the Resume column already says the resume is in, so this line reads "Interested". */
export function ContactStateText({ lead }: { lead: Lead }) {
  if (lead.status !== 'resume_received') return <StatusText lead={lead} />
  return <span className="db-tone is-ok" title="Interested: the resume has been received">Interested</span>
}

/** The assigned recruiter's full name ('' when nobody is assigned). */
export const ownerNameOf = (lead: Lead, userById: LeadColumnContext['userById']) => (lead.ownerId ? (userById(lead.ownerId)?.name ?? lead.ownerName ?? 'Staff') : '')

export function OwnerCell({ lead, userById }: { lead: Lead; userById: LeadColumnContext['userById'] }) {
  const name = ownerNameOf(lead, userById)
  return name
    ? <span className="db-owner" title={name}><Avatar name={name} size="sm" /><span>{name}</span></span>
    : <span className="db-owner is-empty" title="Waiting for a recruiter"><UserCheck size={14} aria-hidden="true" /><span>Unassigned</span></span>
}

/** "Assigned to": the recruiter's full name (wrapping at spaces), the lead status under it, and the next follow-up as a third line only when one is set. */
export function StatusOwnerCell({ lead, userById, onViewRemarks }: { lead: Lead; userById: LeadColumnContext['userById']; onViewRemarks?: (lead: Lead) => void }) {
  const state = followUpState(lead.nextFollowUpAt, lead.status)
  const due = state !== 'none' && state !== 'closed'
  const name = ownerNameOf(lead, userById)
  const body = (
    <>
      {name
        ? <strong className="ld-owner-name" title={name}>{name}</strong>
        : <strong className="ld-owner-name is-empty" title="Waiting for a recruiter">Unassigned</strong>}
      <ContactStateText lead={lead} />
      {due && (
        <small className={cn('ld-follow', state === 'overdue' && 'is-late', state === 'today' && 'is-today')} title={`Follow up ${new Date(lead.nextFollowUpAt as string).toLocaleString()}`}>
          <BellRing size={11} aria-hidden="true" /> {followUpLabel(lead.nextFollowUpAt)}
        </small>
      )}
    </>
  )
  // Only an assigned lead has a history to show; "Unassigned" and every other cell keep the row's click (opens the drawer).
  if (!name || !onViewRemarks) return <span className="db-2l ld-statown">{body}</span>
  return (
    <button type="button" className="db-2l ld-statown ld-statown-btn" title={`See remarks and what ${name} did`} aria-label={`${name}, ${leadStatus(lead.status).label}. See remarks and what ${name} did on this lead`} aria-haspopup="dialog"
      onClick={(event) => { event.stopPropagation(); onViewRemarks(lead) }}>
      {body}
    </button>
  )
}

/** Job title over the muted preferred country, without decorative badges. */
export function PositionCountryCell({ lead }: { lead: Lead }) {
  const country = lead.countryInterest?.trim()
  return (
    <span className="db-2l ld-poscountry">
      <PositionText value={lead.positionApplied} />
      {country ? <small className="db-clamp" title={country}>{country}</small> : null}
    </span>
  )
}

/** One custom question answer (the heading is the sheet's own); long answers wrap on two lines and are whole in the tooltip. */
export function ExtraAnswerCell({ lead, answerKey }: { lead: Lead; answerKey: string }) {
  const text = extraAnswer(lead, answerKey)
  return text ? <span className="db-clamp" title={text}>{text}</span> : <Dash />
}

export function FollowUpCell({ lead }: { lead: Lead }) {
  const state = followUpState(lead.nextFollowUpAt, lead.status)
  if (state === 'none' || state === 'closed') return <Dash />
  const date = new Date(lead.nextFollowUpAt as string)
  return (
    <span className={cn('db-2l ld-follow', state === 'overdue' && 'is-late', state === 'today' && 'is-today')} title={`Follow up ${date.toLocaleString()}`}>
      <strong>{followUpLabel(lead.nextFollowUpAt)}</strong>
      <small>{date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</small>
    </span>
  )
}

/** The green candidate link, or "Resume received" with Convert, or the Upload resume button; a Data Bank match shows above. */
export function ResumeCell({ lead, block, onUpload, onConvert }: { lead: Lead; block: string | null; onUpload: (lead: Lead, file: File) => void; onConvert: (lead: Lead) => void }) {
  const navigate = useNavigate()
  const input = useRef<HTMLInputElement>(null)
  if (lead.candidateId) {
    return (
      <button type="button" className="ld-cand" title="Open the candidate" onClick={(event) => { event.stopPropagation(); navigate(`/candidates/${lead.candidateId}`) }}>
        <span>Candidate</span><b>{lead.candidateRef || 'open'}</b>
      </button>
    )
  }
  if (isClosed(lead.status)) return <Dash />
  const match = lead.matchedCandidateId
    ? <button type="button" className="ld-match" title={`${lead.matchedCandidateName || 'A candidate'} with this phone or email is already in the Data Bank`} onClick={(event) => { event.stopPropagation(); navigate(`/candidates/${lead.matchedCandidateId}`) }}>In Data Bank {lead.matchedCandidateRef ?? ''}</button>
    : null
  if (lead.hasResume) {
    return (
      <span className="ld-resume">
        {match}
        <span className="ld-got" title="Resume received"><CheckCircle2 size={13} aria-hidden="true" /><span>Resume received</span></span>
        <button type="button" className="ld-upload is-primary" disabled={Boolean(block)} title={block ?? 'Make this lead a candidate'} onClick={(event) => { event.stopPropagation(); onConvert(lead) }}>Convert</button>
      </span>
    )
  }
  return (
    <span className="ld-resume" onClick={(event) => event.stopPropagation()}>
      {match}
      <button type="button" className="ld-upload" disabled={Boolean(block)} title={block ?? 'Attach the updated resume the person sent you'} onClick={() => input.current?.click()}>
        <FileUp size={13} aria-hidden="true" />Upload
      </button>
      <input ref={input} type="file" className="sr-only" tabIndex={-1} accept=".pdf,.doc,.docx,.png,.jpg,.jpeg,.webp" aria-label={`Resume for ${lead.name}`}
        onChange={(event) => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ''; if (file) onUpload(lead, file) }} />
    </span>
  )
}

export function LeadActions({ lead, context }: { lead: Lead; context: LeadColumnContext }) {
  return (
    <div className="db-acts is-grid ld-acts" onClick={(event) => event.stopPropagation()}>
      <RowMenu label={`More actions for ${lead.name}`} items={leadMenuItems(context, lead)} />
    </div>
  )
}

/** A lead's WhatsApp button, drawn with the Data Bank's own WhatsApp glyph so both tables match; it opens the message template popover. */
export function LeadWhatsApp({ lead }: { lead: Lead }) {
  return <WhatsAppTemplateButton lead={{ id: lead.id, name: lead.name, phone: lead.phone ? cleanPhone(lead.phone) : undefined, positionApplied: lead.positionApplied }} />
}

/** Education over marital status: two answers of the sheet in one cell. */
export function ProfileCell({ lead }: { lead: Lead }) {
  if (!lead.educationLevel && !lead.maritalStatus) return <Dash />
  return (
    <span className="db-2l" title={[lead.educationLevel, lead.maritalStatus].filter(Boolean).join(' · ')}>
      <span className="db-clamp">{lead.educationLevel || EMPTY_VALUE}</span>
      <small>{lead.maritalStatus || EMPTY_VALUE}</small>
    </span>
  )
}

/** One registry field as a plain cell: up to two lines, whole in the tooltip, a dash when empty. */
export function FieldCell({ lead, field }: { lead: Lead; field: LeadFieldDef }) {
  const value = field.value(lead)
  return value ? <span className="db-clamp" title={value}>{value}</span> : <Dash />
}

/**
 * The panel under an expanded row: every field of the sheet with its own heading, in an aligned grid, then the row's actions.
 * Fields with no answer stay listed (with a dash) so two leads can be compared line by line.
 */
export function LeadExpanded({ lead, extras, context }: { lead: Lead; extras: ExtraColumn[]; context: LeadColumnContext }): ReactNode {
  const rows = leadFieldRows(lead, extras)
  return (
    <div className="ld-xpanel" onClick={(event) => event.stopPropagation()}>
      <dl className="ld-xgrid">
        {rows.map((row) => (
          <div key={row.key} className={cn('ld-xcell', row.custom && 'is-custom')}>
            <dt title={row.label}>{row.label}</dt>
            <dd title={row.value || undefined}>{row.value || <span className="db-muted">{EMPTY_VALUE}</span>}</dd>
          </div>
        ))}
      </dl>
      <div className="ld-xacts">
        <button type="button" className="ld-btn" onClick={() => context.onOpen(lead)}>Open lead</button>
        <button type="button" className="ld-btn" disabled={Boolean(workBlock(context, lead))} title={workBlock(context, lead) ?? undefined} onClick={() => context.onLogCall(lead)}>Add remarks</button>
      </div>
    </div>
  )
}
