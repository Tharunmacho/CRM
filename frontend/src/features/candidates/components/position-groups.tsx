import { useEffect, useState } from 'react'
import { Briefcase, ChevronDown, ChevronRight, UserRoundX } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { api } from '@/shared/api/client'
import type { Candidate } from '@/shared/model/types'
import type { useStore } from '@/shared/state/crm-store'
import { Button } from '@/shared/ui/primitives'
import { CurrentStage, OwnerText, RowActions, StatusText, shortRef } from '@/features/candidates/components/data-bank-columns'
import { Avatar, CountryFlag } from '@/features/candidates/components/db-parts'
import {
  agentName, groupKey, groupMeta, groupResumesPath, groupTitle, highlightParts, resumeCount,
  type PositionGroup,
} from '@/features/candidates/data-bank-search'
import '@/styles/data-bank-positions.css'

type Store = ReturnType<typeof useStore>

const PAGE_SIZE = 25

interface Handlers {
  onOpen: (candidate: Candidate) => void
  onReassign: (candidate: Candidate) => void
  onEdit: (candidate: Candidate) => void
}

/** Text with every searched term marked, so it is clear why a resume matched. */
function Marked({ text, terms }: { text: string; terms: string[] }) {
  return <>{highlightParts(text, terms).map((part, index) => part.hit ? <mark key={index} className="dbp-mark">{part.text}</mark> : <span key={index}>{part.text}</span>)}</>
}

interface ResumesProps extends Handlers {
  /** The whole filter query, so a position lists exactly the resumes the search found. */
  scope: string
  groupId: string
  terms: string[]
  store: Store
  /** Changes whenever the directory was reloaded, so open positions read again. */
  version: number
}

/** The resumes of one open position, loaded on demand (one request per page, never one per resume). */
function GroupResumes({ scope, groupId, terms, store, version, onOpen, onReassign, onEdit }: ResumesProps) {
  const [shown, setShown] = useState<{ items: Candidate[]; total: number; page: number } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError('')
    api.get<{ items: Candidate[]; total: number }>(groupResumesPath(scope, groupId, 0, PAGE_SIZE), { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) setShown({ items: data.items, total: data.total, page: 0 }) })
      .catch(failure => { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'We could not load these resumes.') })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [scope, groupId, version])
  const more = () => {
    if (!shown || loading) return
    setLoading(true); setError('')
    api.get<{ items: Candidate[]; total: number }>(groupResumesPath(scope, groupId, shown.page + 1, PAGE_SIZE))
      .then(data => setShown(current => current && {
        items: [...current.items, ...data.items.filter(item => !current.items.some(known => known.id === item.id))], total: data.total, page: current.page + 1,
      }))
      .catch(failure => setError(failure instanceof Error ? failure.message : 'We could not load more resumes.'))
      .finally(() => setLoading(false))
  }
  if (!shown) {
    return error
      ? <p role="alert" className="dbp-note is-error">{error}</p>
      : <p role="status" className="dbp-note">Loading resumes…</p>
  }
  return (
    <div className="dbp-resumes" role="table" aria-label="Resumes in this position" aria-busy={loading || undefined}>
      <div className="dbp-row is-head" role="row">
        <span role="columnheader">Name</span><span role="columnheader">Phone</span><span role="columnheader">Agent</span>
        <span role="columnheader">Recruiter</span><span role="columnheader">Stage</span><span role="columnheader">Status</span><span role="columnheader" className="sr-only">Actions</span>
      </div>
      {shown.items.map(candidate => {
        const agent = agentName(candidate)
        return (
          <div key={candidate.id} className="dbp-row" role="row" tabIndex={0}
            onClick={() => onOpen(candidate)}
            onKeyDown={event => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onOpen(candidate) } }}>
            <span role="cell" className="dbp-name" data-label="Name">
              <Avatar name={candidate.name || '?'} size="sm" />
              <span className="dbp-name-text">
                <strong title={candidate.name}><Marked text={candidate.name || 'Unnamed'} terms={terms} /></strong>
                <small className="ident" title={`Reference ${candidate.ref}`}>{shortRef(candidate.ref)}</small>
              </span>
            </span>
            <span role="cell" className="ident" data-label="Phone" title={candidate.email || candidate.phone}><Marked text={candidate.phone || '–'} terms={terms} /></span>
            <span role="cell" data-label="Agent" className="dbp-agent" title={agent || 'No agent recorded'}>
              {agent ? <Marked text={agent} terms={terms} /> : <span className="db-muted">–</span>}
            </span>
            <span role="cell" data-label="Recruiter"><OwnerText candidate={candidate} store={store} /></span>
            <span role="cell" data-label="Stage"><CurrentStage candidate={candidate} store={store} /></span>
            <span role="cell" data-label="Status"><StatusText candidate={candidate} /></span>
            <span role="cell" className="dbp-actions"><RowActions candidate={candidate} store={store} onOpen={onOpen} onReassign={onReassign} onEdit={onEdit} /></span>
          </div>
        )
      })}
      {shown.items.length === 0 && <p className="dbp-note">No resumes here for this search.</p>}
      {error && <p role="alert" className="dbp-note is-error">{error}</p>}
      {shown.items.length < shown.total && (
        <div className="dbp-more">
          <Button variant="secondary" disabled={loading} onClick={more}>{loading ? 'Loading…' : `Show more (${shown.total - shown.items.length} left)`}</Button>
        </div>
      )}
    </div>
  )
}

interface Props extends Handlers {
  groups: PositionGroup[]
  scope: string
  terms: string[]
  store: Store
  version: number
  /** A search is active: a few positions open on their own so the matching resumes are visible straight away. */
  autoOpen: boolean
}

/** Positions as expandable groups: title, client, country and resume count; each opens to its resumes. */
export function PositionGroupsList({ groups, scope, terms, store, version, autoOpen, onOpen, onReassign, onEdit }: Props) {
  const [chosen, setChosen] = useState<Record<string, boolean>>({})
  useEffect(() => { setChosen({}) }, [scope])
  return (
    <ul className="dbp-groups" aria-label="Positions">
      {groups.map(group => {
        const key = groupKey(group)
        const open = chosen[key] ?? autoOpen
        const title = groupTitle(group)
        const panelId = `dbp-${key}`
        return (
          <li key={key} className={cn('dbp-group', open && 'is-open', !group.orderId && 'is-none')}>
            <button type="button" className="dbp-head" aria-expanded={open} aria-controls={panelId} onClick={() => setChosen(current => ({ ...current, [key]: !open }))}>
              <span className="dbp-chevron" aria-hidden="true">{open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</span>
              <span className="dbp-icon" aria-hidden="true">{group.orderId ? <Briefcase size={16} /> : <UserRoundX size={16} />}</span>
              <span className="dbp-title">
                <strong><Marked text={title} terms={terms} /></strong>
                <small>
                  {group.country && <CountryFlag country={group.country} />}
                  <span><Marked text={groupMeta(group)} terms={terms} /></span>
                  {group.ref && <span className="ident dbp-ref"><Marked text={group.ref} terms={terms} /></span>}
                </small>
              </span>
              {group.orderStatus && group.orderStatus !== 'open' && <span className="dbp-state">{group.orderStatus.replace('_', ' ')}</span>}
              <span className="dbp-count tnum" title={resumeCount(group.resumes)}><b>{group.resumes}</b> {group.resumes === 1 ? 'resume' : 'resumes'}</span>
            </button>
            <div id={panelId} className="dbp-body" hidden={!open}>
              {open && <GroupResumes scope={scope} groupId={key} terms={terms} store={store} version={version} onOpen={onOpen} onReassign={onReassign} onEdit={onEdit} />}
            </div>
          </li>
        )
      })}
    </ul>
  )
}
