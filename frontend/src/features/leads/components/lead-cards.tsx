import { CheckCircle2, FileUp, PhoneCall, UserPlus } from 'lucide-react'
import { useRef } from 'react'
import { cn } from '@/shared/lib/cn'
import { Checkbox } from '@/shared/ui/primitives'
import { RowMenu } from '@/features/candidates/components/db-parts'
import { createdParts, followUpLabel, followUpState, leadDate, uploadedLabel, type Lead } from '../lead-model'
import { CampaignCell, ContactStateText, EducationCell, ExperienceCell, LocationCell, NameCell, PositionText, ownerNameOf } from './lead-columns'
import { leadMenuItems, workBlock, type LeadColumnContext } from './lead-actions'

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
  const owner = ownerNameOf(lead, context.userById)
  const date = leadDate(lead)
  const created = createdParts(date?.value)
  const uploaded = uploadedLabel(lead.createdAt)
  const follow = followUpState(lead.nextFollowUpAt, lead.status)
  return (
    <li className={cn('db-mini ld-card', fresh && 'is-fresh', follow === 'overdue' && 'is-late')} tabIndex={0} onClick={() => onOpen(lead)}
      onKeyDown={(event) => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onOpen(lead) } }}>
      <div className="db-mini-head">
        <div className="ld-card-who">
          {selectable && <span onClick={(event) => event.stopPropagation()}><Checkbox checked={selected.includes(lead.id)} aria-label={`Select ${lead.name}`} onChange={() => onSelect(selected.includes(lead.id) ? selected.filter((id) => id !== lead.id) : [...selected, lead.id])} /></span>}
          <div className="min-w-0">
            <NameCell lead={lead} />
            {created && <small className="ld-card-sub">{date?.label === 'Imported' ? 'Imported ' : ''}{created.day} {created.year}</small>}
            {date?.label === 'Created' && uploaded && <small className="ld-card-sub">Uploaded {uploaded}</small>}
          </div>
        </div>
        <ContactStateText lead={lead} />
      </div>
      <dl className="db-mini-meta">
        <div><dt>Job</dt><dd><PositionText value={lead.positionApplied} /></dd></div>
        <div><dt>Education</dt><dd><EducationCell lead={lead} /></dd></div>
        <div><dt>India experience</dt><dd><ExperienceCell lead={lead} kind="india" /></dd></div>
        <div><dt>Abroad experience</dt><dd><ExperienceCell lead={lead} kind="abroad" /></dd></div>
        <div><dt>Total experience</dt><dd><ExperienceCell lead={lead} kind="total" /></dd></div>
        <div><dt>Location</dt><dd><LocationCell lead={lead} /></dd></div>
        <div><dt>Campaign</dt><dd><CampaignCell lead={lead} /></dd></div>
        <div><dt>Assigned to</dt><dd>{owner ? <strong className="ld-owner-name" title={owner}>{owner}</strong> : <strong className="ld-owner-name is-empty" title="Waiting for a recruiter">Unassigned</strong>}</dd></div>
        <div><dt>Follow-up</dt><dd className={cn(follow === 'overdue' && 'ld-late-text')}>{follow === 'none' || follow === 'closed' ? '–' : followUpLabel(lead.nextFollowUpAt)}</dd></div>
        <div><dt>Country</dt><dd>{lead.countryInterest || '–'}</dd></div>
      </dl>
      <div className="db-mini-foot" onClick={(event) => event.stopPropagation()}>
        <div className="db-acts">
          <button type="button" className="db-act is-label" disabled={Boolean(block)} title={block ?? 'Add remarks'} onClick={() => context.onLogCall(lead)}><PhoneCall size={14} /><span>Remarks</span></button>
          {lead.candidateId
            ? <span className="ld-got"><CheckCircle2 size={13} aria-hidden="true" />{lead.candidateRef || 'Candidate'}</span>
            : lead.hasResume
              ? <button type="button" className="db-act is-label" disabled={Boolean(block)} title={block ?? 'Convert to candidate'} onClick={() => context.onConvert(lead)}><UserPlus size={14} /><span>Convert</span></button>
              : <button type="button" className="db-act is-label" disabled={Boolean(block)} title={block ?? 'Attach the updated resume'} onClick={() => input.current?.click()}><FileUp size={14} /><span>Resume</span></button>}
          <input ref={input} type="file" className="sr-only" tabIndex={-1} accept=".pdf,.doc,.docx,.png,.jpg,.jpeg,.webp" aria-label={`Resume for ${lead.name}`}
            onChange={(event) => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ''; if (file) context.onUploadResume(lead, file) }} />
          <RowMenu label={`More actions for ${lead.name}`} items={leadMenuItems(context, lead)} />
        </div>
      </div>
    </li>
  )
}
