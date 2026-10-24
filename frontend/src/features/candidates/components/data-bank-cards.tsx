import { dateLabel } from '@/shared/lib/format'
import type { Candidate } from '@/shared/model/types'
import type { useStore } from '@/shared/state/crm-store'
import { normalizedSkills } from '@/features/candidates/candidate-skills'
import { Who } from '@/features/candidates/components/db-parts'
import {
  CandidateActions, CurrentStage, shortRef, MappedOrder, OwnerText, PassportFlag, SourceDot, StatusText, experienceLabel,
} from '@/features/candidates/components/data-bank-columns'

interface Props {
  rows: Candidate[]
  store: ReturnType<typeof useStore>
  onOpen: (candidate: Candidate) => void
  onReassign: (candidate: Candidate) => void
  onEdit: (candidate: Candidate) => void
  onOpenStage: (applicationId: string) => void
}

/** Grid view: one card per candidate carrying the same eleven PRD facts as the list columns. */
export function DataBankCards({ rows, store, onOpen, onReassign, onEdit, onOpenStage }: Props) {
  return (
    <ul className="db-grid" aria-label="Candidates">
      {rows.map((candidate) => {
        const skills = normalizedSkills(candidate.skills)
        return (
          <li key={candidate.id} className="db-mini" tabIndex={0}
            onClick={() => onOpen(candidate)}
            onKeyDown={(event) => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onOpen(candidate) } }}>
            <div className="db-mini-head">
              <Who name={candidate.name} sub={<span className="ident" title={`Reference ${candidate.ref}`}>{shortRef(candidate.ref)}</span>} />
              <StatusText candidate={candidate} />
            </div>
            <dl className="db-mini-meta">
              <div><dt>Job title</dt><dd title={candidate.designations.join(', ')}>{candidate.designations[0] || <span className="db-muted">Not recorded</span>}{candidate.designations.length > 1 && <span className="db-extra"> +{candidate.designations.length - 1}</span>}</dd></div>
              <div><dt>Experience</dt><dd className="tnum">{experienceLabel(candidate.totalExperience)}</dd></div>
              <div><dt>Phone</dt><dd className="ident" title={candidate.email}>{candidate.phone}</dd></div>
              <div><dt>Passport</dt><dd><PassportFlag candidate={candidate} /></dd></div>
              <div><dt>Source</dt><dd><SourceDot source={candidate.source} /></dd></div>
              <div><dt>Recruiter</dt><dd><OwnerText candidate={candidate} store={store} /></dd></div>
              <div><dt>Order</dt><dd><MappedOrder candidate={candidate} store={store} onOpenStage={onOpenStage} /></dd></div>
              <div><dt>Stage</dt><dd><CurrentStage candidate={candidate} store={store} /></dd></div>
            </dl>
            {skills.length > 0 && <p className="db-mini-skills" title={skills.join(', ')}>{skills.slice(0, 4).join(' · ')}{skills.length > 4 ? ` +${skills.length - 4}` : ''}</p>}
            <div className="db-mini-foot">
              <span className="db-mini-when">Added {dateLabel(candidate.addedAt)}</span>
              <CandidateActions candidate={candidate} store={store} onOpen={onOpen} onReassign={onReassign} onEdit={onEdit} />
            </div>
          </li>
        )
      })}
    </ul>
  )
}
