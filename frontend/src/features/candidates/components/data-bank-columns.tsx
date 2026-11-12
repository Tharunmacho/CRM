import { useState, type ReactNode } from 'react'
import { ClipboardList, FileSearch, Footprints, Link2, Mail, MessageCircle, Pencil, Trash2, UserCheck, UserPlus, type LucideIcon } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { applicationFormHref } from '@/features/application-form/form-model'
import { countryCode } from '@/shared/lib/country-flag'
import { labelFor, type FormOptions } from '@/features/form-options/form-options-model'
import { countryTone, dobParts, splitCountries, whatsappHref } from '@/features/candidates/data-bank-view'
import { stageByNo } from '@/shared/workflow/stages'
import type { Candidate } from '@/shared/model/types'
import { ownerLabel } from '@/features/candidates/data-bank-pools'
import { candidateReviewIssues } from '@/features/candidates/candidate-review'
import { CandidateReviewPopover } from '@/features/candidates/components/candidate-review-popover'
import { Avatar, RowMenu, StageTracker, StatusDot, type DotTone, type RowMenuItem } from '@/features/candidates/components/db-parts'
import type { DbColumn } from '@/features/candidates/components/db-table'
import { canDeleteCandidate } from '@/features/candidates/profile-actions'
import { DeleteCandidateDialog } from '@/features/candidates/components/delete-candidate-dialog'
import type { useStore } from '@/shared/state/crm-store'
import { workRefusal } from '@/features/candidates/candidate-access'
import { BANK_FIELD_DEFS, DEFAULT_COLUMN_FIELDS, FIELD_PREFIX, type BankContext, type BankFieldDef } from '@/features/candidates/data-bank-fields'
import { bankApplication, bankFormApplication } from '@/features/candidates/data-bank-assignment'
import { BANK_TABLE_COLUMNS, PLACEHOLDER_NAME, agentLabel, bankColumnWidth, candidateSubline, isPlaceholderName, sourceProvenance } from '@/features/candidates/data-bank-table'
import '@/styles/data-bank-table.css'

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

/** Pipeline status stays visible even when profile details need a separate review. */
export function candidateStatus(candidate: Candidate): { label: string; tone: DotTone } {
  const key = candidate.status
  return STATUS_VIEW[key] ?? { label: key, tone: 'quiet' }
}

/** Long generated references read as their first 12 characters; the full value stays in the tooltip. */
export const shortRef = (ref: string) => (ref.length > 12 ? `${ref.slice(0, 12)}…` : ref)
const Dash = () => <span className="db-muted" aria-label="None">–</span>

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
/** "22 Oct 26": one short line for a table cell (the full date is in the tooltip). */
export const shortDate = (value: string) => { const d = new Date(value); return `${d.getDate()} ${MONTHS[d.getMonth()]} ${String(d.getFullYear()).slice(2)}` }

/** Where the profile came from: a small icon beside the channel name. */
export function SourceDot({ source }: { source: string }) {
  const Icon = SOURCE_ICON[source] ?? Link2
  return <span className={`db-source is-${source}`}><Icon size={15} aria-hidden="true" /><span>{SOURCE_LABEL[source] ?? source}</span></span>
}

/** Intake channel, with human provenance for imports and Lead Sheet conversions. */
export function SourceLine({ candidate }: { candidate: Candidate }) {
  const label = SOURCE_LABEL[candidate.source] ?? candidate.source
  const detail = sourceProvenance(candidate.source, candidate.sourceDetail)
  return <span className="dbt-stack" title={[label, detail].filter(Boolean).join(' · ')}>
    <span className="dbt-main">{label}</span>
    {detail && <small className="dbt-sub">{detail}</small>}
  </span>
}

/** Table form of the source: the icon alone, with the channel in a tooltip and for screen readers. */
export function SourceIcon({ source }: { source: string }) {
  const Icon = SOURCE_ICON[source] ?? Link2
  const label = SOURCE_LABEL[source] ?? source
  return <span className={`db-icon-chip db-source is-${source}`} title={`Source: ${label}`}><Icon size={15} aria-hidden="true" /><span className="sr-only">{label}</span></span>
}

export function StatusText({ candidate }: { candidate: Candidate }) {
  const status = candidateStatus(candidate)
  const issues = candidateReviewIssues(candidate)
  const primary = <StatusDot tone={status.tone} title={status.label}>{status.label}</StatusDot>
  if (!issues.length) return primary
  return <span className="db-status-summary">
    {candidate.status !== 'needs_review' && primary}
    <CandidateReviewPopover candidateId={candidate.id} name={candidate.name} issues={issues} />
  </span>
}

export function OwnerText({ candidate, store }: { candidate: Candidate; store: Store }) {
  const owner = candidate.ownerId ? store.user(candidate.ownerId) : undefined
  return owner
    ? <span className="db-owner" title={owner.name}><Avatar name={owner.name} size="sm" /><span>{owner.name.split(' ')[0]}</span></span>
    : <span className="db-owner is-empty" title="No recruiter assigned yet"><UserCheck size={14} aria-hidden="true" /><span>{ownerLabel(null)}</span></span>
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

const humanize = (value: string) => { const text = value.replace(/_/g, ' ').trim(); return text.charAt(0).toUpperCase() + text.slice(1) }

/** What the field registry (data-bank-fields.ts) needs from the store to write a candidate's recruiter, stage, order, status and option labels. */
export function bankContext(store: Store, options: FormOptions = {}): BankContext {
  return {
    ownerName: (id) => (id ? store.user(id)?.name ?? '' : ''),
    agent: (candidate) => agentLabel(candidate.sourceDetail, candidate.source),
    statusLabel: (candidate) => [candidate.status === 'needs_review' ? '' : candidateStatus(candidate).label,
      ...candidateReviewIssues(candidate).map(issue => issue.label)].filter(Boolean).join(' · '),
    sourceLabel: (source, detail) => [SOURCE_LABEL[source] ?? source, sourceProvenance(source, detail)].filter(Boolean).join(' · '),
    optionLabel: (category, value) => { const label = labelFor(options, category, value); return label === value ? humanize(value) : label },
    application: (candidateId) => {
      const app = bankApplication(store.applicationsFor(candidateId))
      if (!app) return null
      return { stage: stageByNo(app.currentStage).title, order: store.order(app.orderId)?.ref ?? '' }
    },
  }
}

/** One registry field as a plain single-line cell: whole in the tooltip, a dash when empty. */
export function FieldCell({ candidate, field, context }: { candidate: Candidate; field: BankFieldDef; context: BankContext }) {
  const value = field.value(candidate, context)
  return value ? <span className="db-clamp" title={value}>{value}</span> : <Dash />
}

/** Open the displayed placement's form, retaining access to closed-only application history. */
export function formOrderId(store: Store, candidateId: string): string | undefined {
  return bankFormApplication(store.applicationsFor(candidateId))?.orderId
}

/** The always-visible way into a candidate's application form (icon and label, never inside a menu). */
export function ApplicationFormAction({ candidate, store, label = 'Application form' }: { candidate: Candidate; store: Store; label?: string }) {
  const navigate = useNavigate()
  return (
    <button type="button" className="db-act is-label is-form" title={`Open ${candidate.name}'s application form`} aria-label={`Open application form for ${candidate.name}`}
      onClick={() => navigate(applicationFormHref(candidate.id, formOrderId(store, candidate.id)))}><ClipboardList size={14} /><span>{label}</span></button>
  )
}

/** Cards and the expanded row: labelled Open, Application form and Assign plus an edit icon (all with tooltips). */
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

/** Table and job-group rows: an "open profile" icon button and one "..." menu holding Application form, Assign and Edit. */
export function RowActions({ candidate, store, onOpen, onReassign, onEdit }: Handlers & { candidate: Candidate; store: Store }) {
  const navigate = useNavigate()
  const [deleting, setDeleting] = useState(false)
  const refusal = workRefusal(store.me, candidate, store.user(candidate.ownerId)?.name)
  const items: RowMenuItem[] = [
    { label: 'Open profile', icon: FileSearch, onSelect: () => navigate(`/candidates/${candidate.id}`) },
  ]
  if (store.can('reassign')) items.push({ label: 'Assign to a recruiter', icon: UserCheck, onSelect: () => onReassign(candidate) })
  items.push({ label: 'Edit profile', icon: Pencil, onSelect: () => onEdit(candidate), disabledReason: refusal ?? undefined })
  return (
    <div className="db-acts is-grid" onClick={(event) => event.stopPropagation()}>
      <button type="button" className="db-act" title={`Open ${candidate.name}'s application form`} aria-label={`Open application form for ${candidate.name}`} onClick={() => onOpen(candidate)}><ClipboardList size={15} aria-hidden="true" /></button>
      {canDeleteCandidate(store.me.role) && (
        <button type="button" className="db-act is-danger" title={`Delete ${candidate.name}`} aria-label={`Delete ${candidate.name}`} onClick={() => setDeleting(true)}><Trash2 size={15} aria-hidden="true" /></button>
      )}
      <RowMenu label={`More actions for ${candidate.name}`} items={items} />
      {deleting && <DeleteCandidateDialog open candidateId={candidate.id} name={candidate.name} onClose={() => setDeleting(false)} onDeleted={() => setDeleting(false)} />}
    </div>
  )
}

/* ---------------------------------------------------------------- the seven cells (table, job groups and cards share them) */

interface CellProps {
  candidate: Candidate
  /** Marks the searched terms inside a text (job groups); the text as it is when omitted. */
  mark?: (text: string) => ReactNode
}
const plain = (text: string): ReactNode => text

/** Name in bold over the reference and date of birth in muted text. A name the intake could not read shows muted and italic with a "Needs review" pill. */
export function CandidateCell({ candidate, mark = plain }: CellProps) {
  const dob = dobParts(candidate.dob)
  const sub = candidateSubline(candidate.ref, dob?.label)
  const unread = isPlaceholderName(candidate.name)
  return (
    <span className="dbt-stack" title={`${unread ? PLACEHOLDER_NAME : candidate.name}${dob ? ` · born ${dob.label} (${dob.age} years)` : ''} · ${candidate.ref}`}>
      <span className="dbt-line">
        {unread
          ? <em className="dbt-main is-unread">Name missing or unreadable</em>
          : <strong className="dbt-main">{mark(candidate.name)}</strong>}
      </span>
      {sub && <small className="dbt-sub">{sub}</small>}
    </span>
  )
}

/** Phone (with the green WhatsApp glyph when a WhatsApp number is saved) over the e-mail; both on one line each, cut with a tooltip. */
export function ContactLines({ candidate, mark = plain }: CellProps) {
  return (
    <span className="dbt-stack" title={candidate.email ? `${candidate.phone} · ${candidate.email}` : candidate.phone}>
      <span className="dbt-line">
        {candidate.phone ? <span className="dbt-main ident">{mark(candidate.phone)}</span> : <Dash />}
        {whatsappHref(candidate.whatsapp) && <WhatsAppLink candidate={candidate} />}
      </span>
      {candidate.email && <small className="dbt-sub" title={candidate.email}>{mark(candidate.email)}</small>}
    </span>
  )
}

/** The agent or agency that sent the profile; a muted dash when none was recorded (internal pointers are never shown). */
export function AgentLine({ candidate, mark = plain }: CellProps) {
  const agent = agentLabel(candidate.sourceDetail, candidate.source)
  return agent ? <span className="dbt-main dbt-agent" title={`Agency: ${agent}`}>{mark(agent)}</span> : <span className="db-muted" title="No agency recorded">–</span>
}

/** The compact 11-stage indicator; with `onOpenStage` it opens the stage modal. A dash while the candidate has no order. */
export function StageCell({ candidate, store, onOpenStage }: { candidate: Candidate; store: Store; onOpenStage?: (applicationId: string) => void }) {
  const app = bankApplication(store.applicationsFor(candidate.id))
  if (!app) return <Dash />
  if (!onOpenStage) return <StageChip stage={app.currentStage} />
  return (
    <button type="button" className="dbt-stage" title={`See the 11 stages for ${store.orderLabel(app.orderId)}`}
      onClick={(event) => { event.stopPropagation(); onOpenStage(app.id) }}
      onKeyDown={(event) => event.stopPropagation()}>
      <StageChip stage={app.currentStage} />
    </button>
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
 * The Data Bank table: Candidate, Contact, Source, Agent, Recruiter, Status, Actions. The widths come from one list
 * (data-bank-table.ts) that the job groups and the cards read too. Every column is left-aligned (Actions right) with the
 * same padding in the header and the body. Everything else the profile holds (preferred country, passport, job, documents on
 * file ...) is in the expanded row, and in the Columns menu as columns that start switched off.
 */
export function dataBankColumns({ store, onOpen, onReassign, onEdit, options }: ColumnContext): DbColumn<Candidate>[] {
  const context = bankContext(store, options)
  const spec = Object.fromEntries(BANK_TABLE_COLUMNS.map((column) => [column.key, column]))
  const base = (key: (typeof BANK_TABLE_COLUMNS)[number]['key']) => ({ key, header: spec[key].header, sortKey: spec[key].sortKey, width: bankColumnWidth(spec[key]), align: spec[key].align })
  const core: DbColumn<Candidate>[] = [
    { ...base('candidate'), group: 'Person', render: (c) => <CandidateCell candidate={c} /> },
    { ...base('contact'), group: 'Contact', render: (c) => <ContactLines candidate={c} /> },
    { ...base('source'), group: 'Workflow', render: (c) => <SourceLine candidate={c} /> },
    { ...base('agent'), group: 'Workflow', render: (c) => <AgentLine candidate={c} /> },
    { ...base('owner'), group: 'Workflow', render: (c) => <OwnerText candidate={c} store={store} /> },
    { ...base('status'), group: 'Workflow', render: (c) => <StatusText candidate={c} /> },
    { ...base('actions'), render: (c) => <RowActions candidate={c} store={store} onOpen={onOpen} onReassign={onReassign} onEdit={onEdit} /> },
  ]
  // Preferred country is off the default table; it stays one click away in the Columns menu and always in the expanded row.
  const country: DbColumn<Candidate> = {
    key: 'country', group: 'Screening', header: 'Preferred country', sortKey: 'country', width: 130, align: 'left', optional: 10, off: true,
    render: (c) => <CountryPills value={c.countryPreference} />,
  }
  // The rest of the registry: columns that start switched off (Columns menu); every field is also in the expanded row.
  const fields: DbColumn<Candidate>[] = BANK_FIELD_DEFS.filter((field) => field.inColumns && !DEFAULT_COLUMN_FIELDS.has(field.id)).map((field, index) => ({
    key: `${FIELD_PREFIX}${field.id}`, header: field.label, sortKey: field.sortKey, group: field.group, width: field.width, align: 'left' as const, optional: 20 + index, off: true,
    render: (c: Candidate) => <FieldCell candidate={c} field={field} context={context} />,
  }))
  // Actions stays the last column whatever the person switches on.
  const actions = core.pop()!
  return [...core, country, ...fields, actions]
}
