import { useRef, useState, type ReactNode } from 'react'
import { Check, CheckCircle2, Copy, FileUp, Instagram, Facebook, PhoneCall, UserCheck, UserPlus, UserRoundX, MessageCircle, FileSearch, Globe, Link2, ListChecks, Trash2, BellRing } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { cn } from '@/shared/lib/cn'
import { countryTone, whatsappHref } from '@/features/candidates/data-bank-view'
import { CountryPills } from '@/features/candidates/components/data-bank-columns'
import { WhatsAppTemplateButton, openWhatsApp } from './wa-template'
import { Avatar, RowMenu, type RowMenuItem } from '@/features/candidates/components/db-parts'
import type { DbColumn } from '@/features/candidates/components/db-table'
import type { User } from '@/shared/model/types'
import {
  EXTRA_PREFIX, LEAD_HEADINGS, cleanPhone, createdParts, dobLabel, experienceLines, extraAnswer, followUpLabel, followUpState, isClosed, leadRefusal, leadStatus,
  platformKind, PLATFORM_LABEL, type ExtraColumn, type Lead, type LeadActor,
} from '../lead-model'
import { GATE_REASON, type GateState, gateBlocks } from '../lead-gate'
import { DEFAULT_COLUMN_FIELDS, EMPTY_VALUE, FIELD_PREFIX, LEAD_FIELD_DEFS, leadFieldRows, type LeadFieldDef } from '../lead-fields'

const Dash = () => <span className="db-muted" aria-label="None">–</span>

export interface LeadColumnContext {
  me: LeadActor
  gate: GateState
  userById: (id: string | null | undefined) => User | undefined
  canAssign: boolean
  onOpen: (lead: Lead) => void
  onLogCall: (lead: Lead) => void
  onConvert: (lead: Lead) => void
  onUploadResume: (lead: Lead, file: File) => void
  onAssign: (lead: Lead) => void
  onNotInterested: (lead: Lead) => void
  /** Row menu "Change status" (opens the status dialog; an undo toast follows). */
  onChangeStatus?: (lead: Lead) => void
  /** Admin only: delete one lead (row menu and drawer). */
  canDelete?: boolean
  onDelete?: (lead: Lead) => void
}

/** The reason a work action on this lead is unavailable: the daily-work gate first, then ownership. */
export function workBlock(context: Pick<LeadColumnContext, 'me' | 'gate' | 'userById'>, lead: Lead): string | null {
  if (gateBlocks(context.gate)) return GATE_REASON
  return leadRefusal(context.me, lead, context.userById(lead.ownerId)?.name)
}

export function CreatedCell({ lead }: { lead: Lead }) {
  const parts = createdParts(lead.createdTime)
  if (!parts) return <Dash />
  return <span className="db-2l" title={`Created ${new Date(lead.createdTime as string).toLocaleString()}`}><strong className="db-num">{parts.day}</strong><small>{parts.year}</small></span>
}

export function NameCell({ lead }: { lead: Lead }) {
  const dob = dobLabel(lead.dob)
  return (
    <span className="db-2l is-name" title={`${lead.name}${dob ? ` · born ${dob}` : ''}${lead.gender ? ` · ${lead.gender}` : ''}`}>
      <strong className="db-clamp">{lead.name || 'Unnamed'}</strong>
      {dob || lead.gender ? <small>{[dob, lead.gender].filter(Boolean).join(' · ')}</small> : <small className="db-muted">No date of birth</small>}
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

/** The position as a solid pill whose colour is stable for that text. */
export function PositionPill({ value }: { value?: string | null }) {
  const text = (value ?? '').trim()
  if (!text) return <Dash />
  return <span className="db-cpills" title={text}><span className={`db-cpill is-c${countryTone(text)}`}>{text}</span></span>
}

export function ExperienceCell({ lead }: { lead: Lead }) {
  const { restaurant, total } = experienceLines(lead)
  if (!restaurant && !total) return <Dash />
  return (
    <span className="db-2l" title={`Restaurant ${restaurant ?? 'not given'} · total ${total ?? 'not given'}`}>
      <strong className="db-num">{restaurant ?? '–'}</strong>
      <small>{total ? `${total} total` : 'total not given'}</small>
    </span>
  )
}

export function LocationCell({ lead }: { lead: Lead }) {
  if (!lead.city && !lead.state) return <Dash />
  return (
    <span className="db-2l" title={[lead.city, lead.state].filter(Boolean).join(', ')}>
      <strong className="db-clamp">{lead.city || lead.state}</strong>
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

export function OwnerCell({ lead, userById }: { lead: Lead; userById: LeadColumnContext['userById'] }) {
  const name = lead.ownerId ? (userById(lead.ownerId)?.name ?? lead.ownerName ?? 'Staff') : ''
  return name
    ? <span className="db-owner" title={name}><Avatar name={name} size="sm" /><span>{name.split(' ')[0]}</span></span>
    : <span className="db-owner is-empty" title="Waiting for a recruiter"><UserCheck size={14} aria-hidden="true" /><span>Unassigned</span></span>
}

/** The status word over who is working the lead, and the next follow-up as a third line only when one is set. */
export function StatusOwnerCell({ lead, userById }: { lead: Lead; userById: LeadColumnContext['userById'] }) {
  const state = followUpState(lead.nextFollowUpAt, lead.status)
  const due = state !== 'none' && state !== 'closed'
  return (
    <span className="db-2l ld-statown">
      <StatusText lead={lead} />
      <OwnerCell lead={lead} userById={userById} />
      {due && (
        <small className={cn('ld-follow', state === 'overdue' && 'is-late', state === 'today' && 'is-today')} title={`Follow up ${new Date(lead.nextFollowUpAt as string).toLocaleString()}`}>
          <BellRing size={11} aria-hidden="true" /> {followUpLabel(lead.nextFollowUpAt)}
        </small>
      )}
    </span>
  )
}

/** The position pill with the country the person wants to go to under it. */
export function PositionCountryCell({ lead }: { lead: Lead }) {
  return (
    <span className="db-2l ld-poscountry">
      <PositionPill value={lead.positionApplied} />
      {lead.countryInterest ? <CountryPills value={lead.countryInterest} max={1} /> : null}
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
        <span className="ld-got" title="Resume received"><CheckCircle2 size={13} aria-hidden="true" />Resume in</span>
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

export function leadMenuItems(context: LeadColumnContext, lead: Lead): RowMenuItem[] {
  const block = workBlock(context, lead)
  const convert = lead.candidateId ? 'This lead is already a candidate.' : block ?? undefined
  const wa = whatsappHref(lead.phone)
  const items: RowMenuItem[] = [
    { label: 'Open lead', icon: FileSearch, onSelect: () => context.onOpen(lead) },
    { label: 'Log a call', icon: PhoneCall, onSelect: () => context.onLogCall(lead), disabledReason: block ?? undefined },
    { label: 'Convert to candidate', icon: UserPlus, onSelect: () => context.onConvert(lead), disabledReason: convert },
  ]
  if (context.canAssign) items.push({ label: 'Assign', icon: UserCheck, onSelect: () => context.onAssign(lead) })
  items.push({ label: 'WhatsApp message…', icon: MessageCircle, onSelect: () => { if (wa) openWhatsApp(lead) }, disabledReason: wa ? undefined : 'No valid phone number' })
  const converted = lead.candidateId || lead.status === 'converted' ? 'This lead is already a candidate.' : undefined
  if (context.onChangeStatus) items.push({ label: 'Change status', icon: ListChecks, onSelect: () => context.onChangeStatus?.(lead), disabledReason: block ?? converted })
  items.push({ label: 'Not interested', icon: UserRoundX, onSelect: () => context.onNotInterested(lead), disabledReason: block ?? converted })
  if (context.canDelete && context.onDelete) items.push({ label: 'Delete lead', icon: Trash2, onSelect: () => context.onDelete?.(lead), disabledReason: lead.candidateId ? 'This lead was converted to a candidate, so it cannot be deleted.' : undefined })
  return items
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
        <button type="button" className="ld-btn" disabled={Boolean(workBlock(context, lead))} title={workBlock(context, lead) ?? undefined} onClick={() => context.onLogCall(lead)}>Log a call</button>
      </div>
    </div>
  )
}

/**
 * The Lead sheet table: one table that always fits its box (DbTable `fit`). Widths are the least a column needs to show its
 * content in full; the table shares what is left over. Several sheet fields share one cell (name over date of birth and gender,
 * phone with WhatsApp over e-mail, city over state, restaurant over total years, campaign over form, status over assignee).
 * When the minimums do not add up the table first tightens its spacing, then hides the columns with the highest `optional`
 * number; the Columns menu, the expandable row, the drawer and the cards still show every field. The rest of the sheet's
 * fields (see lead-fields.ts) and its custom questions (`extras`) are columns that start switched off.
 */
export function leadColumns(context: LeadColumnContext, extras: ExtraColumn[] = []): DbColumn<Lead>[] {
  const h = LEAD_HEADINGS
  const core: DbColumn<Lead>[] = [
    { key: 'actions', header: h.actions, width: 104, sticky: 'start', render: (l) => <LeadActions lead={l} context={context} /> },
    { key: 'created', header: h.created, sortKey: 'created', width: 92, optional: 3, group: 'Workflow', sticky: 'start', render: (l) => <CreatedCell lead={l} /> },
    { key: 'name', header: h.name, sortKey: 'name', width: 164, group: 'Person', sticky: 'start', render: (l) => <NameCell lead={l} /> },
    { key: 'contact', header: h.contact, width: 214, group: 'Contact', render: (l) => <ContactCell lead={l} /> },
    { key: 'position', header: h.position, sortKey: 'position', width: 140, group: 'Job answers', render: (l) => <PositionCountryCell lead={l} /> },
    { key: 'experience', header: h.experience, width: 90, optional: 2, group: 'Job answers', render: (l) => <ExperienceCell lead={l} /> },
    { key: 'location', header: h.location, width: 116, optional: 1, group: 'Contact', render: (l) => <LocationCell lead={l} /> },
    { key: 'campaign', header: h.campaign, sortKey: 'campaign', width: 164, optional: 4, group: 'Campaign & ad', render: (l) => <CampaignCell lead={l} /> },
    { key: 'status', header: h.status, sortKey: 'status', width: 110, group: 'Workflow', render: (l) => <StatusOwnerCell lead={l} userById={context.userById} /> },
    { key: 'resume', header: h.resume, width: 92, group: 'Workflow', render: (l) => <ResumeCell lead={l} block={workBlock(context, l)} onUpload={context.onUploadResume} onConvert={context.onConvert} /> },
    { key: 'profile', header: 'Education / Marital status', width: 130, optional: 7, off: true, group: 'Person', render: (l) => <ProfileCell lead={l} /> },
  ]
  const fields: DbColumn<Lead>[] = LEAD_FIELD_DEFS.filter((field) => !DEFAULT_COLUMN_FIELDS.has(field.id)).map((field, index) => ({
    key: `${FIELD_PREFIX}${field.id}`, header: field.label, width: field.width, optional: 20 + index, off: true, group: field.group,
    render: (l: Lead) => <FieldCell lead={l} field={field} />,
  }))
  const questions: DbColumn<Lead>[] = extras.map((column, index) => ({
    key: `${EXTRA_PREFIX}${column.key}`, header: column.label, width: 140, optional: 60 + index, off: true, group: 'Job answers',
    render: (l) => <ExtraAnswerCell lead={l} answerKey={column.key} />,
  }))
  return [...core, ...fields, ...questions]
}
