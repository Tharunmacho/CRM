import { ClipboardList, ExternalLink, FileSearch, Footprints, Link2, Mail, MessageCircle, Pencil, ShieldCheck, ShieldOff, UserCheck, UserPlus, type LucideIcon } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { applicationFormHref } from '@/features/application-form/form-model'
import { cn } from '@/shared/lib/cn'
import { countryCode } from '@/shared/lib/country-flag'
import { dateLabel } from '@/shared/lib/format'
import { FORM_OPTION_CATEGORIES, labelFor, toneStyle, type FormOptions } from '@/features/form-options/form-options-model'
import { useFormOptions } from '@/features/form-options/use-form-options'
import { INTEREST_TONE, SERVICE_CHARGE_TONE, countryTone, createdParts, dobParts, optionTone, splitCountries, whatsappHref, type Tone } from '@/features/candidates/data-bank-view'
import { stageByNo } from '@/shared/workflow/stages'
import type { Candidate } from '@/shared/model/types'
import { normalizedSkills } from '@/features/candidates/candidate-skills'
import { ownerLabel } from '@/features/candidates/data-bank-pools'
import { Avatar, RowMenu, StageTracker, StatusDot, type DotTone, type RowMenuItem } from '@/features/candidates/components/db-parts'
import type { DbColumn } from '@/features/candidates/components/db-table'
import type { useStore } from '@/shared/state/crm-store'
import { agentName } from '@/features/candidates/data-bank-search'
import { workRefusal } from '@/features/candidates/candidate-access'
import { BANK_FIELD_DEFS, DEFAULT_COLUMN_FIELDS, FIELD_PREFIX, type BankContext, type BankFieldDef } from '@/features/candidates/data-bank-fields'

export const SOURCE_LABEL: Record<string, string> = {
  email: 'Email',
  whatsapp: 'WhatsApp',
  walk_in: 'Walk-in',
  referral: 'Referral',
  other: 'Other',
}

const SOURCE_ICON: Record<string, LucideIcon> = { email: Mail, whatsapp: MessageCircle, walk_in: Footprints, referral: UserPlus, other: Link2 }

const STATUS_VIEW: Record<string, { label: string; tone: DotTone }> = {
  new: { label: 'New', tone: 'info' },
  in_process: { label: 'In process', tone: 'info' },
  not_interested: { label: 'Not interested', tone: 'quiet' },
  deployed: { label: 'Deployed', tone: 'ok' },
  needs_review: { label: 'Needs review', tone: 'warn' },
}

type Store = ReturnType<typeof useStore>

/** The status a recruiter reads: a flagged profile reads as needing review whatever its pipeline status is. */
export function candidateStatus(candidate: Candidate): { label: string; tone: DotTone } {
  const key = candidate.needsReview && candidate.status !== 'needs_review' ? 'needs_review' : candidate.status
  return STATUS_VIEW[key] ?? { label: key, tone: 'quiet' }
}

/** Long generated references read as their first 12 characters; the full value stays in the tooltip. */
export const shortRef = (ref: string) => (ref.length > 12 ? `${ref.slice(0, 12)}…` : ref)
const Dash = () => <span className="db-muted" aria-label="None">–</span>

export const experienceLabel = (years: number) => `${years} ${years === 1 ? 'yr' : 'yrs'}`
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
/** "22 Oct 26": one short line for a table cell (the full date is in the tooltip). */
export const shortDate = (value: string) => { const d = new Date(value); return `${d.getDate()} ${MONTHS[d.getMonth()]} ${String(d.getFullYear()).slice(2)}` }

/** Where the profile came from: a small icon beside the channel name. */
export function SourceDot({ source }: { source: string }) {
  const Icon = SOURCE_ICON[source] ?? Link2
  return <span className={`db-source is-${source}`}><Icon size={15} aria-hidden="true" /><span>{SOURCE_LABEL[source] ?? source}</span></span>
}

/** Table form of the source: the icon alone, with the channel in a tooltip and for screen readers. */
export function SourceIcon({ source }: { source: string }) {
  const Icon = SOURCE_ICON[source] ?? Link2
  const label = SOURCE_LABEL[source] ?? source
  return <span className={`db-icon-chip db-source is-${source}`} title={`Source: ${label}`}><Icon size={15} aria-hidden="true" /><span className="sr-only">{label}</span></span>
}

export function StatusText({ candidate }: { candidate: Candidate }) {
  const status = candidateStatus(candidate)
  return <StatusDot tone={status.tone} title={status.label}>{status.label}</StatusDot>
}

export function PassportFlag({ candidate }: { candidate: Candidate }) {
  return (
    <span className={candidate.passportNo ? 'db-badge is-yes' : 'db-badge'} title={candidate.passportNo ? `Passport ${candidate.passportNo}` : 'No passport number saved'}>
      {candidate.passportNo ? 'Yes' : 'No'}
    </span>
  )
}

/** Table form of the passport flag: a shield with a tooltip, the answer also spelled out for screen readers. */
export function PassportIcon({ candidate }: { candidate: Candidate }) {
  const has = !!candidate.passportNo
  const Icon = has ? ShieldCheck : ShieldOff
  return (
    <span className={`db-icon-chip db-passport ${has ? 'is-yes' : ''}`} title={has ? `Passport number saved: ${candidate.passportNo}` : 'No passport number saved'}>
      <Icon size={15} aria-hidden="true" /><span className="sr-only">{has ? 'Yes' : 'No'}</span>
    </span>
  )
}

export function OwnerText({ candidate, store }: { candidate: Candidate; store: Store }) {
  const owner = candidate.ownerId ? store.user(candidate.ownerId) : undefined
  return owner
    ? <span className="db-owner" title={owner.name}><Avatar name={owner.name} size="sm" /><span>{owner.name.split(' ')[0]}</span></span>
    : <span className="db-owner is-empty" title="Waiting for a recruiter"><UserCheck size={14} aria-hidden="true" /><span>{ownerLabel(null)}</span></span>
}

export function StageChip({ stage }: { stage: number }) {
  return <StageTracker stage={stage} title={stageByNo(stage).title} />
}

interface Handlers {
  onOpen: (candidate: Candidate) => void
  onReassign: (candidate: Candidate) => void
  onEdit: (candidate: Candidate) => void
}
interface ColumnContext extends Handlers {
  store: Store
  onOpenStage: (applicationId: string) => void
  /** The admin's form-option labels (service charges, interest level...), for the registry columns; the stored value is shown when absent. */
  options?: FormOptions
}

/** What the field registry (data-bank-fields.ts) needs from the store to write a candidate's recruiter, stage, order, status and option labels. */
export function bankContext(store: Store, options: FormOptions = {}): BankContext {
  return {
    ownerName: (id) => (id ? store.user(id)?.name ?? '' : ''),
    agent: (candidate) => agentName(candidate) ?? '',
    statusLabel: (candidate) => candidateStatus(candidate).label,
    sourceLabel: (source) => SOURCE_LABEL[source] ?? source,
    optionLabel: (category, value) => { const label = labelFor(options, category, value); return label === value ? humanize(value) : label },
    application: (candidateId) => {
      const app = store.activeApplicationFor(candidateId)
      if (!app) return null
      return { stage: stageByNo(app.currentStage).title, order: store.order(app.orderId)?.ref ?? '' }
    },
  }
}

/** One registry field as a plain cell: up to two lines, whole in the tooltip, a dash when empty. */
export function FieldCell({ candidate, field, context }: { candidate: Candidate; field: BankFieldDef; context: BankContext }) {
  const value = field.value(candidate, context)
  return value ? <span className="db-clamp" title={value}>{value}</span> : <Dash />
}

/** The order a candidate's application form opens on: the live application, else the most recent one. */
export function formOrderId(store: Store, candidateId: string): string | undefined {
  const applications = store.applicationsFor(candidateId)
  return (applications.find((application) => application.status === 'active') ?? applications[applications.length - 1])?.orderId
}

/** The always-visible way into a candidate's application form (icon and label, never inside a menu). */
export function ApplicationFormAction({ candidate, store, label = 'Application form' }: { candidate: Candidate; store: Store; label?: string }) {
  const navigate = useNavigate()
  return (
    <button type="button" className="db-act is-label is-form" title={`Open ${candidate.name}'s application form`} aria-label={`Open application form for ${candidate.name}`}
      onClick={() => navigate(applicationFormHref(candidate.id, formOrderId(store, candidate.id)))}><ClipboardList size={14} /><span>{label}</span></button>
  )
}

/** Cards: labelled Open, Application form and Assign plus an edit icon (all with tooltips). */
export function CandidateActions({ candidate, store, onOpen, onReassign, onEdit }: Handlers & { candidate: Candidate; store: Store }) {
  const refusal = workRefusal(store.me, candidate, store.user(candidate.ownerId)?.name)
  return (
    <div className="db-acts" onClick={(event) => event.stopPropagation()}>
      <button type="button" className="db-act is-label" title={`Open ${candidate.name}'s profile`} aria-label={`Open ${candidate.name}`} onClick={() => onOpen(candidate)}><FileSearch size={14} /><span>Open</span></button>
      <ApplicationFormAction candidate={candidate} store={store} />
      {store.can('reassign') && <button type="button" className="db-act is-label" title={`Assign ${candidate.name} to a recruiter`} aria-label={`Reassign ${candidate.name}`} onClick={() => onReassign(candidate)}><UserCheck size={14} /><span>Assign</span></button>}
      <button type="button" className="db-act" disabled={Boolean(refusal)} title={refusal ?? `Edit ${candidate.name}'s profile`} aria-label={`Edit ${candidate.name}`} onClick={() => onEdit(candidate)}><Pencil size={14} /></button>
    </div>
  )
}

/** Table: the application-form shortcut and one "..." menu holding Open, Application form, Assign and Edit. */
export function RowActions({ candidate, store, onOpen, onReassign, onEdit }: Handlers & { candidate: Candidate; store: Store }) {
  const navigate = useNavigate()
  const refusal = workRefusal(store.me, candidate, store.user(candidate.ownerId)?.name)
  const items: RowMenuItem[] = [
    { label: 'Open profile', icon: FileSearch, onSelect: () => onOpen(candidate) },
    { label: 'Application form', icon: ClipboardList, onSelect: () => navigate(applicationFormHref(candidate.id, formOrderId(store, candidate.id))) },
  ]
  if (store.can('reassign')) items.push({ label: 'Assign to a recruiter', icon: UserCheck, onSelect: () => onReassign(candidate) })
  items.push({ label: 'Edit profile', icon: Pencil, onSelect: () => onEdit(candidate), disabledReason: refusal ?? undefined })
  return (
    <div className="db-acts is-grid" onClick={(event) => event.stopPropagation()}>
      <ApplicationFormAction candidate={candidate} store={store} label="Form" />
      <RowMenu label={`More actions for ${candidate.name}`} items={items} />
    </div>
  )
}

/** The mapped order link (opens the stage modal) with the company underneath, or a muted "Not mapped". */
export function MappedOrder({ candidate, store, onOpenStage, withCompany = true }: { candidate: Candidate; store: Store; onOpenStage: (id: string) => void; withCompany?: boolean }) {
  const app = store.activeApplicationFor(candidate.id)
  const order = app ? store.order(app.orderId) : undefined
  if (!app) return withCompany ? <span className="db-muted">No order yet</span> : <Dash />
  return (
    <div className="db-cell" onClick={(event) => event.stopPropagation()}>
      <button type="button" className="db-order-link" onClick={() => onOpenStage(app.id)} title={`See the 11 stages for ${order ? store.orderLabel(order.id) : 'this order'}`}>
        {order?.ref ?? 'Order'}{withCompany && <ExternalLink size={11} aria-hidden="true" />}
      </button>
      {withCompany && order && <p className="db-sub" title={store.orderLabel(order.id)}>{store.company(order.companyId)?.name ?? store.designation(order.designationId)?.title ?? ''}</p>}
    </div>
  )
}

export function CurrentStage({ candidate, store }: { candidate: Candidate; store: Store }) {
  const app = store.activeApplicationFor(candidate.id)
  if (!app) return <Dash />
  return <StageChip stage={app.currentStage} />
}


/* ---------------------------------------------------------------- grid-table cells */

/** "04 Nov" over "2026". */
export function CreatedCell({ candidate }: { candidate: Candidate }) {
  const parts = createdParts(candidate.addedAt)
  if (!parts) return <Dash />
  return <span className="db-2l" title={`Added ${dateLabel(candidate.addedAt)}`}><strong className="db-num">{parts.day}</strong><small>{parts.year}</small></span>
}

/** Name in bold over the date of birth (the reference when no date of birth is saved). */
export function NameDobCell({ candidate }: { candidate: Candidate }) {
  const dob = dobParts(candidate.dob)
  return (
    <span className="db-2l is-name" title={`${candidate.name}${dob ? ` · born ${dob.label} (${dob.age} years)` : ''} · ${candidate.ref}`}>
      <strong className="db-clamp">{candidate.name || 'Unnamed'}</strong>
      {dob ? <small>{dob.label}</small> : <small className="ident">{shortRef(candidate.ref)}</small>}
    </span>
  )
}

/** Phone with the green WhatsApp glyph beside it (when a WhatsApp number is saved), over the e-mail. */
export function ContactCell({ candidate }: { candidate: Candidate }) {
  return (
    <span className="db-2l ld-contact" title={candidate.email ? `${candidate.phone} · ${candidate.email}` : candidate.phone}>
      <span className="ld-line"><span className="ident db-num">{candidate.phone}</span>{whatsappHref(candidate.whatsapp) && <WhatsAppLink candidate={candidate} />}</span>
      {candidate.email && <small className="db-ellip" title={candidate.email}>{candidate.email}</small>}
    </span>
  )
}

/** Yes in green, No muted; the number is in the tooltip. */
export function PassportText({ candidate }: { candidate: Candidate }) {
  const has = !!candidate.passportNo
  return <span className={cn('db-tone', has ? 'is-ok' : 'is-quiet')} title={has ? `Passport ${candidate.passportNo}` : 'No passport number saved'}>{has ? 'Yes' : 'No'}</span>
}

/** First job preference over the years of experience; the rest of the list and the skills are in the tooltip. */
export function JobCell({ candidate }: { candidate: Candidate }) {
  const skills = normalizedSkills(candidate.skills)
  const extra = candidate.designations.length - 1
  return (
    <span className="db-2l" title={[candidate.designations.join(', '), skills.length ? `Skills: ${skills.join(', ')}` : ''].filter(Boolean).join('\n')}>
      <span className="db-clamp is-strong">{candidate.designations[0] || <Dash />}{extra > 0 && <span className="db-extra"> +{extra}</span>}</span>
      <small>{experienceLabel(candidate.totalExperience)} exp</small>
    </span>
  )
}

/** The preferred country as a solid coloured pill (one pill per country written; the colour is stable per country). */
export function CountryPills({ value, max = 2 }: { value?: string | null; max?: number }) {
  const countries = splitCountries(value)
  if (countries.length === 0) return <Dash />
  return (
    <span className="db-cpills" title={countries.join(', ')}>
      {countries.slice(0, max).map(country => <span key={country} className={`db-cpill is-c${countryTone(country, countryCode(country))}`}>{country}</span>)}
      {countries.length > max && <span className="db-extra">+{countries.length - max}</span>}
    </span>
  )
}

const humanize = (value: string) => { const text = value.replace(/_/g, ' ').trim(); return text.charAt(0).toUpperCase() + text.slice(1) }

/** A stored form-option value as coloured, bold text: the admin's label and tone, or the built-in meaning. */
function OptionText({ category, value, fallback }: { category: string; value?: string | null; fallback: Record<string, Tone> }) {
  const { options } = useFormOptions()
  if (!value) return <Dash />
  const option = (options[category] ?? []).find(item => item.value === value)
  const label = option?.label ?? humanize(value)
  const { tone, color } = optionTone(toneStyle(option?.tone), value, fallback)
  return <span className={cn('db-tone', `is-${tone}`)} style={color ? { color } : undefined} title={option?.hint ? `${label}: ${option.hint}` : label}>{label}</span>
}

export const ServiceChargesText = ({ candidate }: { candidate: Candidate }) =>
  <OptionText category={FORM_OPTION_CATEGORIES.serviceCharges} value={candidate.serviceChargesStatus} fallback={SERVICE_CHARGE_TONE} />
export const InterestText = ({ candidate }: { candidate: Candidate }) =>
  <OptionText category={FORM_OPTION_CATEGORIES.interestLevel} value={candidate.interestLevel} fallback={INTEREST_TONE} />

/** Agent / referrer over how the profile came in. */
export function AgentCell({ candidate }: { candidate: Candidate }) {
  const agent = agentName(candidate)
  return (
    <span className="db-2l" title={agent ? `Agent / referred by: ${agent}` : 'No agent recorded'}>
      <span className="db-clamp">{agent || <Dash />}</span>
      <small><SourceDot source={candidate.source} /></small>
    </span>
  )
}

/** Stage tracker with the mapped order's reference under it. */
export function StageOrderCell({ candidate, store, onOpenStage }: { candidate: Candidate; store: Store; onOpenStage: (id: string) => void }) {
  const app = store.activeApplicationFor(candidate.id)
  if (!app) return <span className="db-muted" title="Not matched to an order yet">No order yet</span>
  return (
    <span className="db-2l is-stage">
      <StageChip stage={app.currentStage} />
      <MappedOrder candidate={candidate} store={store} onOpenStage={onOpenStage} withCompany={false} />
    </span>
  )
}

/** Green WhatsApp glyph that opens the chat; a dash when no WhatsApp number is saved. */
export function WhatsAppLink({ candidate }: { candidate: Candidate }) {
  const href = whatsappHref(candidate.whatsapp)
  if (!href) return <span className="db-muted" title="No WhatsApp number saved" aria-label="No WhatsApp number">–</span>
  return (
    <a className="db-wa" href={href} target="_blank" rel="noopener noreferrer" title={`Chat on WhatsApp: ${candidate.whatsapp}`} aria-label={`Open WhatsApp chat with ${candidate.name}`}
      onClick={event => event.stopPropagation()} onKeyDown={event => event.stopPropagation()}>
      <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" focusable="false">
        <path fill="currentColor" d="M17.47 14.38c-.3-.15-1.76-.87-2.03-.97-.27-.1-.47-.15-.67.15-.2.3-.77.97-.94 1.16-.17.2-.35.22-.65.07-.3-.15-1.26-.46-2.39-1.48-.88-.79-1.48-1.76-1.65-2.06-.17-.3-.02-.46.13-.6.13-.14.3-.35.45-.52.15-.17.2-.3.3-.5.1-.2.05-.37-.03-.52-.07-.15-.67-1.61-.92-2.21-.24-.58-.49-.5-.67-.51h-.57c-.2 0-.52.07-.79.37-.27.3-1.04 1.02-1.04 2.48 0 1.46 1.07 2.88 1.21 3.07.15.2 2.1 3.2 5.08 4.49.71.3 1.26.49 1.69.63.71.23 1.36.2 1.87.12.57-.08 1.76-.72 2-1.41.25-.7.25-1.29.17-1.41-.07-.12-.27-.2-.57-.35M12.05 21.78h-.01a9.87 9.87 0 0 1-5.03-1.38l-.36-.21-3.74.98 1-3.65-.24-.37a9.86 9.86 0 0 1-1.51-5.26c0-5.45 4.44-9.88 9.89-9.88 2.64 0 5.12 1.03 6.99 2.9a9.82 9.82 0 0 1 2.89 6.99c0 5.45-4.44 9.88-9.88 9.88M20.46 3.49A11.8 11.8 0 0 0 12.05 0C5.5 0 .16 5.34.16 11.89c0 2.1.55 4.14 1.59 5.95L.06 24l6.3-1.65a11.9 11.9 0 0 0 5.69 1.45h.01c6.55 0 11.89-5.34 11.89-11.89 0-3.18-1.24-6.17-3.48-8.42" />
      </svg>
    </a>
  )
}

/**
 * The Data Bank grid table: actions, created, name and date of birth, contact, passport, job preferred, preferred country,
 * service charges, recruiter, interest level, agent and source, stage and order, status, WhatsApp. Every PRD fact is
 * still on the row: experience under the job, source under the agent, the mapped order under the stage.
 * Widths are minimums: the table fills the card and scrolls sideways when it is narrower; actions, created and name stay pinned
 * on the left (as many as leave room) and WhatsApp on the right.
 */
export function dataBankColumns({ store, onOpen, onReassign, onEdit, onOpenStage, options }: ColumnContext): DbColumn<Candidate>[] {
  const context = bankContext(store, options)
  const core: DbColumn<Candidate>[] = [
    { key: 'actions', header: 'Actions', width: 140, sticky: 'start', render: (c) => <RowActions candidate={c} store={store} onOpen={onOpen} onReassign={onReassign} onEdit={onEdit} /> },
    { key: 'created', group: 'Workflow', header: 'Created', sortKey: 'added', width: 92, optional: 2, sticky: 'start', render: (c) => <CreatedCell candidate={c} /> },
    { key: 'name', group: 'Person', header: 'Name / DOB', sortKey: 'name', width: 164, sticky: 'start', render: (c) => <NameDobCell candidate={c} /> },
    { key: 'contact', group: 'Contact', header: 'Contact', sortKey: 'phone', width: 214, render: (c) => <ContactCell candidate={c} /> },
    { key: 'passport', group: 'Passport', header: 'Passport', sortKey: 'passport', width: 76, optional: 3, render: (c) => <PassportText candidate={c} /> },
    { key: 'job', group: 'Job & experience', header: 'Job preferred', sortKey: 'designation', width: 140, render: (c) => <JobCell candidate={c} /> },
    { key: 'country', group: 'Screening', header: 'Preferred country', sortKey: 'country', width: 130, render: (c) => <CountryPills value={c.countryPreference} /> },
    { key: 'service', group: 'Screening', header: 'Service charges', sortKey: 'service', width: 104, optional: 5, render: (c) => <ServiceChargesText candidate={c} /> },
    { key: 'owner', group: 'Workflow', header: 'Recruiter', sortKey: 'owner', width: 104, optional: 1, render: (c) => <OwnerText candidate={c} store={store} /> },
    { key: 'interest', group: 'Screening', header: 'Interest level', sortKey: 'interest', width: 104, optional: 4, render: (c) => <InterestText candidate={c} /> },
    { key: 'agent', group: 'Workflow', header: 'Agent', width: 104, optional: 6, render: (c) => <AgentCell candidate={c} /> },
    { key: 'stage', group: 'Workflow', header: 'Stage / Order', sortKey: 'stage', width: 150, render: (c) => <StageOrderCell candidate={c} store={store} onOpenStage={onOpenStage} /> },
    { key: 'status', group: 'Workflow', header: 'Status', sortKey: 'status', width: 124, render: (c) => <StatusText candidate={c} /> },
  ]
  // The rest of the registry: columns that start switched off (Columns menu); every field is also in the expandable row.
  const fields: DbColumn<Candidate>[] = BANK_FIELD_DEFS.filter((field) => field.inColumns && !DEFAULT_COLUMN_FIELDS.has(field.id)).map((field, index) => ({
    key: `${FIELD_PREFIX}${field.id}`, header: field.label, sortKey: field.sortKey, group: field.group, width: field.width, optional: 20 + index, off: true,
    render: (c: Candidate) => <FieldCell candidate={c} field={field} context={context} />,
  }))
  return [...core, ...fields]
}
