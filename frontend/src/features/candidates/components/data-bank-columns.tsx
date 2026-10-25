import { ExternalLink, FileSearch, Footprints, Link2, Mail, MessageCircle, Pencil, ShieldCheck, ShieldOff, UserCheck, UserPlus, type LucideIcon } from 'lucide-react'
import { dateLabel } from '@/shared/lib/format'
import { stageByNo } from '@/shared/workflow/stages'
import type { Candidate } from '@/shared/model/types'
import { normalizedSkills } from '@/features/candidates/candidate-skills'
import { ownerLabel } from '@/features/candidates/data-bank-pools'
import { Avatar, RowMenu, StageTracker, StatusDot, type DotTone, type RowMenuItem } from '@/features/candidates/components/db-parts'
import type { DbColumn } from '@/features/candidates/components/db-table'
import type { useStore } from '@/shared/state/crm-store'

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
}

/** Cards: labelled Open and Assign plus an edit icon (all with tooltips). */
export function CandidateActions({ candidate, store, onOpen, onReassign, onEdit }: Handlers & { candidate: Candidate; store: Store }) {
  const canWork = store.can('work_any_candidate') || candidate.ownerId === store.me.id
  return (
    <div className="db-acts" onClick={(event) => event.stopPropagation()}>
      <button type="button" className="db-act is-label" title={`Open ${candidate.name}'s profile`} aria-label={`Open ${candidate.name}`} onClick={() => onOpen(candidate)}><FileSearch size={14} /><span>Open</span></button>
      {store.can('reassign') && <button type="button" className="db-act is-label" title={`Assign ${candidate.name} to a recruiter`} aria-label={`Reassign ${candidate.name}`} onClick={() => onReassign(candidate)}><UserCheck size={14} /><span>Assign</span></button>}
      {canWork && <button type="button" className="db-act" title={`Edit ${candidate.name}'s profile`} aria-label={`Edit ${candidate.name}`} onClick={() => onEdit(candidate)}><Pencil size={14} /></button>}
    </div>
  )
}

/** Table: one Open icon button and a "..." menu holding Assign and Edit. */
export function RowActions({ candidate, store, onOpen, onReassign, onEdit }: Handlers & { candidate: Candidate; store: Store }) {
  const canWork = store.can('work_any_candidate') || candidate.ownerId === store.me.id
  const items: RowMenuItem[] = []
  if (store.can('reassign')) items.push({ label: 'Assign to a recruiter', icon: UserCheck, onSelect: () => onReassign(candidate) })
  if (canWork) items.push({ label: 'Edit profile', icon: Pencil, onSelect: () => onEdit(candidate) })
  return (
    <div className="db-acts" onClick={(event) => event.stopPropagation()}>
      <button type="button" className="db-act" title={`Open ${candidate.name}'s profile`} aria-label={`Open ${candidate.name}`} onClick={() => onOpen(candidate)}><FileSearch size={15} /></button>
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

/**
 * The eleven PRD columns, each its own column, in order: name, phone, job title, experience,
 * passport, source, recruiter, order, stage, status, date added; then the row
 * actions. Widths are design widths that become shares of the table, so they fit the card.
 */
export function dataBankColumns({ store, onOpen, onReassign, onEdit, onOpenStage }: ColumnContext): DbColumn<Candidate>[] {
  return [
    {
      key: 'name', header: 'Name', sortKey: 'name', width: 104,
      render: (c) => (
        <span className="db-who is-compact">
          <Avatar name={c.name} size="sm" />
          <span className="db-who-text"><strong title={c.name}>{c.name}</strong><small className="ident" title={`Reference ${c.ref}`}>{shortRef(c.ref)}</small></span>
        </span>
      ),
    },
    {
      key: 'phone', header: 'Phone', sortKey: 'phone', width: 122,
      render: (c) => <span className="ident db-num" title={c.email ? `${c.phone} · ${c.email}` : c.phone}>{c.phone}</span>,
    },
    {
      key: 'designation', header: 'Job title', sortKey: 'designation', width: 84,
      render: (c) => {
        const skills = normalizedSkills(c.skills)
        return (
          <span className="db-wrap" title={[c.designations.join(', '), skills.length ? `Skills: ${skills.join(', ')}` : ''].filter(Boolean).join('\n')}>
            {c.designations[0] || <Dash />}
            {c.designations.length > 1 && <span className="db-extra"> +{c.designations.length - 1}</span>}
          </span>
        )
      },
    },
    { key: 'experience', header: 'Exp.', sortKey: 'experience', width: 54, render: (c) => <span className="tnum db-nowrap" title={`${c.totalExperience} years of experience`}>{experienceLabel(c.totalExperience)}</span> },
    { key: 'passport', header: 'Passport', sortKey: 'passport', width: 64, render: (c) => <PassportIcon candidate={c} /> },
    { key: 'source', header: 'Source', sortKey: 'source', width: 50, render: (c) => <SourceIcon source={c.source} /> },
    { key: 'owner', header: 'Recruiter', sortKey: 'owner', width: 106, render: (c) => <OwnerText candidate={c} store={store} /> },
    { key: 'order', header: 'Order', sortKey: 'order', width: 90, render: (c) => <MappedOrder candidate={c} store={store} onOpenStage={onOpenStage} withCompany={false} /> },
    { key: 'stage', header: 'Stage', sortKey: 'stage', width: 118, render: (c) => <CurrentStage candidate={c} store={store} /> },
    { key: 'status', header: 'Status', sortKey: 'status', width: 118, render: (c) => <StatusText candidate={c} /> },
    { key: 'added', header: 'Added', sortKey: 'added', width: 86, render: (c) => <span className="db-nowrap" title={dateLabel(c.addedAt)}>{shortDate(c.addedAt)}</span> },
    { key: 'actions', header: '', width: 84, align: 'right', render: (c) => <RowActions candidate={c} store={store} onOpen={onOpen} onReassign={onReassign} onEdit={onEdit} /> },
  ]
}
