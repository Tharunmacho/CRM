import { travellerIdOf, type CandidateIds } from '@/features/candidates/candidate-ids'
import '@/styles/candidate-ids.css'

/** "Traveller ID CI-T-26-27-001" as a quiet chip; nothing at all for a candidate who has not travelled. */
export function TravellerIdChip({ candidate, className }: { candidate: CandidateIds | null | undefined; className?: string }) {
  const id = travellerIdOf(candidate)
  if (!id) return null
  return (
    <span className={className ? `cid-chip ${className}` : 'cid-chip'} title={`Traveller ID ${id}`}>
      <span>Traveller ID</span><b className="tnum">{id}</b>
    </span>
  )
}
