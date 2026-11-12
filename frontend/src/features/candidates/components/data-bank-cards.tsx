import type { Candidate } from '@/shared/model/types'
import type { useStore } from '@/shared/state/crm-store'
import { agentLabel } from '@/features/candidates/data-bank-table'
import { AgentLine, CandidateActions, CandidateCell, ContactLines, OwnerText, StageCell, StatusText } from '@/features/candidates/components/data-bank-columns'

interface Props {
  rows: Candidate[]
  store: ReturnType<typeof useStore>
  onOpen: (candidate: Candidate) => void
  onReassign: (candidate: Candidate) => void
  onEdit: (candidate: Candidate) => void
  /** Opens the 11-stage modal from the stage indicator. */
  onOpenStage?: (applicationId: string) => void
}

/** Cards (phones and the card view): name and status on top; contact, recruiter and stage below; the actions at the foot. Same cells as the table. */
export function DataBankCards({ rows, store, onOpen, onReassign, onEdit, onOpenStage }: Props) {
  return (
    <ul className="db-grid" aria-label="Candidates">
      {rows.map((candidate) => (
        <li key={candidate.id} className="db-mini" tabIndex={0}
          onClick={() => onOpen(candidate)}
          onKeyDown={(event) => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onOpen(candidate) } }}>
          <div className="db-mini-head">
            <CandidateCell candidate={candidate} />
            <StatusText candidate={candidate} />
          </div>
          <dl className="db-mini-meta">
            <div className="is-wide"><dt>Contact</dt><dd><ContactLines candidate={candidate} /></dd></div>
            <div><dt>Recruiter</dt><dd><OwnerText candidate={candidate} store={store} /></dd></div>
            <div><dt>Stage</dt><dd><StageCell candidate={candidate} store={store} onOpenStage={onOpenStage} /></dd></div>
            {agentLabel(candidate.sourceDetail, candidate.source) && <div className="is-wide"><dt>Agent</dt><dd><AgentLine candidate={candidate} /></dd></div>}
          </dl>
          <div className="db-mini-foot">
            <CandidateActions candidate={candidate} store={store} onOpen={onOpen} onReassign={onReassign} onEdit={onEdit} />
          </div>
        </li>
      ))}
    </ul>
  )
}
