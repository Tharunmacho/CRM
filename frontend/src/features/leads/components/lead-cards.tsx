import { CheckCircle2, FileUp, PhoneCall, UserPlus } from 'lucide-react'
import { useRef } from 'react'
import { cn } from '@/shared/lib/cn'
import { Checkbox } from '@/shared/ui/primitives'
import { RowMenu } from '@/features/candidates/components/db-parts'
import { cleanPhone, createdParts, followUpLabel, followUpState, type Lead } from '../lead-model'
import { CopyButton, LeadWhatsApp, OwnerCell, PositionPill, StatusText, leadMenuItems, workBlock, type LeadColumnContext } from './lead-columns'

/** Stacked cards for narrow screens: the same lead facts and actions as the table row. */
export function LeadCards({ rows, context, onOpen, selectable, selected, onSelect, freshBatch }: {
  rows: Lead[]
  context: LeadColumnContext
  onOpen: (lead: Lead) => void
  selectable: boolean
  selected: string[]
  onSelect: (ids: string[]) => void
  freshBatch: string
}) {
  return (
    <ul className="db-grid ld-cards" aria-label="Leads">
      {rows.map((lead) => <LeadCard key={lead.id} lead={lead} context={context} onOpen={onOpen} selectable={selectable} selected={selected} onSelect={onSelect} fresh={Boolean(freshBatch) && lead.batchId === freshBatch} />)}
    </ul>
  )
}

function LeadCard({ lead, context, onOpen, selectable, selected, onSelect, fresh }: { lead: Lead; context: LeadColumnContext; onOpen: (lead: Lead) => void; selectable: boolean; selected: string[]; onSelect: (ids: string[]) => void; fresh: boolean }) {
  const input = useRef<HTMLInputElement>(null)
  const block = workBlock(context, lead)
  const created = createdParts(lead.createdTime)
  const follow = followUpState(lead.nextFollowUpAt, lead.status)
  const phone = cleanPhone(lead.phone)
  return (
    <li className={cn('db-mini ld-card', fresh && 'is-fresh', follow === 'overdue' && 'is-late')} tabIndex={0} onClick={() => onOpen(lead)}
      onKeyDown={(event) => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onOpen(lead) } }}>
      <div className="db-mini-head">
        <div className="ld-card-who">
          {selectable && <span onClick={(event) => event.stopPropagation()}><Checkbox checked={selected.includes(lead.id)} aria-label={`Select ${lead.name}`} onChange={() => onSelect(selected.includes(lead.id) ? selected.filter((id) => id !== lead.id) : [...selected, lead.id])} /></span>}
          <div className="min-w-0">
            <strong className="ld-card-name">{lead.name || 'Unnamed'}</strong>
            <small className="ld-card-sub">{created ? `${created.day} ${created.year}` : ''}{lead.city ? ` · ${lead.city}` : ''}</small>
          </div>
        </div>
        <StatusText lead={lead} />
      </div>
      <dl className="db-mini-meta">
        <div><dt>Position</dt><dd><PositionPill value={lead.positionApplied} /></dd></div>
        <div><dt>Phone</dt><dd className="ld-copyrow">{phone ? <><span className="ident">{phone}</span><CopyButton value={phone} label="Copy phone" /></> : '–'}</dd></div>
        <div><dt>Assigned to</dt><dd><OwnerCell lead={lead} userById={context.userById} /></dd></div>
        <div><dt>Follow-up</dt><dd className={cn(follow === 'overdue' && 'ld-late-text')}>{follow === 'none' || follow === 'closed' ? '–' : followUpLabel(lead.nextFollowUpAt)}</dd></div>
        <div><dt>Campaign</dt><dd title={lead.campaignName ?? ''}>{lead.campaignName || '–'}</dd></div>
        <div><dt>Country</dt><dd>{lead.countryInterest || '–'}</dd></div>
      </dl>
      <div className="db-mini-foot" onClick={(event) => event.stopPropagation()}>
        <div className="db-acts">
          <button type="button" className="db-act is-label" disabled={Boolean(block)} title={block ?? 'Log a call'} onClick={() => context.onLogCall(lead)}><PhoneCall size={14} /><span>Call</span></button>
          {lead.candidateId
            ? <span className="ld-got"><CheckCircle2 size={13} aria-hidden="true" />{lead.candidateRef || 'Candidate'}</span>
            : lead.hasResume
              ? <button type="button" className="db-act is-label" disabled={Boolean(block)} title={block ?? 'Convert to candidate'} onClick={() => context.onConvert(lead)}><UserPlus size={14} /><span>Convert</span></button>
              : <button type="button" className="db-act is-label" disabled={Boolean(block)} title={block ?? 'Attach the updated resume'} onClick={() => input.current?.click()}><FileUp size={14} /><span>Resume</span></button>}
          <input ref={input} type="file" className="sr-only" tabIndex={-1} accept=".pdf,.doc,.docx,.png,.jpg,.jpeg,.webp" aria-label={`Resume for ${lead.name}`}
            onChange={(event) => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ''; if (file) context.onUploadResume(lead, file) }} />
          <LeadWhatsApp lead={lead} />
          <RowMenu label={`More actions for ${lead.name}`} items={leadMenuItems(context, lead)} />
        </div>
      </div>
    </li>
  )
}
