import { useEffect, useState, type CSSProperties } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { api } from '@/shared/api/client'
import type { Candidate } from '@/shared/model/types'
import type { useStore } from '@/shared/state/crm-store'
import { Button } from '@/shared/ui/primitives'
import { AgentLine, CandidateCell, ContactLines, OwnerText, RowActions, SourceLine, StatusText } from '@/features/candidates/components/data-bank-columns'
import { BANK_TABLE_COLUMNS, bankGridTemplate, groupClientLine, groupHeading } from '@/features/candidates/data-bank-table'
import {
  groupKey, groupResumesPath, highlightParts, resumeCount,
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
  const mark = (text: string) => <Marked text={text} terms={terms} />
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
    <div className="dbp-resumes" role="table" aria-label="Resumes in this job" aria-busy={loading || undefined} style={{ '--dbp-cols': bankGridTemplate() } as CSSProperties}>
      <div className="dbp-row is-head" role="row">
        {BANK_TABLE_COLUMNS.map(column => <span key={column.key} role="columnheader" data-col={column.key}>{column.header}</span>)}
      </div>
      {shown.items.map(candidate => (
        <div key={candidate.id} className="dbp-row" role="row" tabIndex={0}
          onClick={() => onOpen(candidate)}
          onKeyDown={event => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onOpen(candidate) } }}>
          <span role="cell" data-col="candidate" data-label="Candidate"><CandidateCell candidate={candidate} mark={mark} /></span>
          <span role="cell" data-col="contact" data-label="Contact"><ContactLines candidate={candidate} mark={mark} /></span>
          <span role="cell" data-col="source" data-label="Source"><SourceLine candidate={candidate} /></span>
          <span role="cell" data-col="agent" data-label="Agent"><AgentLine candidate={candidate} mark={mark} /></span>
          <span role="cell" data-col="owner" data-label="Recruiter"><OwnerText candidate={candidate} store={store} /></span>
          <span role="cell" data-col="status" data-label="Status"><StatusText candidate={candidate} /></span>
          <span role="cell" data-col="actions"><RowActions candidate={candidate} store={store} onOpen={onOpen} onReassign={onReassign} onEdit={onEdit} /></span>
        </div>
      ))}

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
    <ul className="dbp-groups" aria-label="Jobs">
      {groups.map(group => {
        const key = groupKey(group)
        const open = chosen[key] ?? autoOpen
        const title = groupHeading(group)
        const client = groupClientLine(group)
        const panelId = `dbp-${key}`
        return (
          <li key={key} className={cn('dbp-group', open && 'is-open', !group.orderId && 'is-none')}>
            <button type="button" className="dbp-head" aria-expanded={open} aria-controls={panelId} onClick={() => setChosen(current => ({ ...current, [key]: !open }))}>
              <span className="dbp-chevron" aria-hidden="true">{open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</span>
              <strong className="dbp-title" title={group.ref ? `${title} (${group.ref})` : title}><Marked text={title} terms={terms} /></strong>
              {client && <span className="dbp-client" title={client}><Marked text={client} terms={terms} /></span>}
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
