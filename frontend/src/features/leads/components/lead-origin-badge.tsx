import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Megaphone } from 'lucide-react'
import { leadExtrasApi, type LeadOrigin } from '../lead-extras-api'
import { originLabel } from '../lead-origin'
import '@/styles/lead-extras.css'

/**
 * On a candidate's profile: "From lead sheet: <campaign>", linking back to that lead's drawer. Shown when the candidate was converted
 * from a lead, or when a stored lead has the same phone or e-mail (late matching). Renders nothing otherwise, and nothing while loading.
 */
export function LeadOriginBadge({ candidateId }: { candidateId: string }) {
  const [leads, setLeads] = useState<LeadOrigin[]>([])
  useEffect(() => {
    const controller = new AbortController()
    setLeads([])
    leadExtrasApi.originOf(candidateId, controller.signal).then((body) => { if (!controller.signal.aborted) setLeads(body.leads ?? []) }).catch(() => { /* the badge is a convenience */ })
    return () => controller.abort()
  }, [candidateId])
  if (leads.length === 0) return null
  return (
    <p className="ld-origin" aria-label="Lead sheet origin">
      {leads.map((lead) => (
        <Link key={lead.id} to={`/leads?lead=${encodeURIComponent(lead.id)}`}
          title={lead.converted ? 'This candidate was converted from this lead. Open the lead.' : 'A lead with the same phone or e-mail is on the lead sheet. Open the lead.'}>
          <Megaphone size={13} aria-hidden="true" /><span>{originLabel(lead)}</span>
        </Link>
      ))}
    </p>
  )
}
