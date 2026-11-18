import { useState } from 'react'
import { Plane } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { Button } from '@/shared/ui/primitives'
import { EmptyState } from '@/shared/ui/display'
import { RecordIdentity } from '@/shared/ui/workspace'
import { dateLabel } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { useTravelled } from '@/features/travelled/travelled-api'
import { EMPTY_FILTERS, type TravelledRow } from '@/features/travelled/travelled-model'
import { OwnerTag } from './task-identity'
import { columnLayout, ScrollEdges } from './daily-table'

const SHOWN = 15
const COLUMNS = [
  { key: 'no', title: 'S NO', width: 40 },
  { key: 'candidate', title: 'CANDIDATE NAME', width: 180, left: true },
  { key: 'client', title: 'CLIENT / COUNTRY', width: 150, left: true },
  { key: 'staff', title: 'Recruiter', width: 100, supervisor: true, left: true },
  { key: 'job', title: 'JOB', width: 130, left: true },
  { key: 'date', title: 'TRAVEL DATE', width: 100 },
  { key: 'documents', title: 'DOCUMENTS', width: 130 },
  { key: 'actions', title: 'Actions', width: 100, quiet: true },
] as const

/** The recruiter filter of the Tasks page applies by name: a travelled row carries the recruiter's name, not an id. */
export function TravelledPanel({ supervisor, recruiterName, onOpen }: { supervisor: boolean; recruiterName: string; onOpen: (row: TravelledRow) => void }) {
  const store = useStore()
  const { data } = useTravelled(EMPTY_FILTERS, store.me.id)
  const [all, setAll] = useState(false)
  if (!data) return null
  const rows = (data.rows ?? []).filter((row) => !recruiterName || (row.recruiter ?? '').trim().toLowerCase() === recruiterName.trim().toLowerCase())
  const columns = COLUMNS.filter((c) => supervisor || !('supervisor' in c))
  const layout = columnLayout(columns)
  const visible = all ? rows : rows.slice(0, SHOWN)
  return (
    <section className="dw-panel" aria-label="Travelled" id="travelled">
      <div className="dw-panel-head">
        <div><h2><Plane size={16} aria-hidden="true" /> Travelled</h2>
          <p className="dw-summary">{rows.length} {rows.length === 1 ? 'candidate' : 'candidates'}</p></div>
      </div>
      {rows.length === 0 ? <EmptyState title="Nobody has travelled yet" description="Candidates appear here once they are deployed." /> : (
        <ScrollEdges label="Travelled candidates, scroll sideways for more columns">
          <table className="dw-table dw-main" aria-label="Travelled candidates" style={{ minWidth: layout.minWidth }}>
            <colgroup>{columns.map((c) => <col key={c.key} style={{ width: layout.share(c) }} />)}</colgroup>
            <thead><tr>{columns.map((c) => <th key={c.key} scope="col" className={cn('left' in c && 'is-left', c.key === 'actions' && 'is-actions')}>{'quiet' in c ? <span className="sr-only">{c.title}</span> : c.title}</th>)}</tr></thead>
            <tbody>
              {visible.map((row, index) => (
                <tr key={row.applicationId} className={cn('dw-row', index % 2 === 1 && 'is-alt')}>
                  <td className="is-num tnum" data-label="S NO">{index + 1}</td>
                  <td className="is-left" data-label="CANDIDATE NAME"><RecordIdentity name={row.name} detail={row.careId ?? row.ref} /></td>
                  <td className="is-left" data-label="CLIENT / COUNTRY">{row.client}{row.country ? ` · ${row.country}` : ''}</td>
                  {supervisor && <td className="is-left" data-label="Recruiter">{row.recruiter ? <OwnerTag name={row.recruiter} /> : <span className="dw-none">—</span>}</td>}
                  <td className={cn('is-left', !row.job && 'is-empty')} data-label="JOB">{row.job ?? '—'}</td>
                  <td data-label="TRAVEL DATE">{row.travelDate ? dateLabel(row.travelDate) : '—'}</td>
                  <td data-label="DOCUMENTS" title={row.documents?.missing.length ? `Missing: ${row.documents.missing.join(', ')}` : undefined}>
                    {row.documents ? `${row.documents.have} of ${row.documents.required}` : '—'}</td>
                  <td className="is-actions" data-label="Actions"><div className="dw-actions"><Button size="sm" variant="primary" className="dw-act" onClick={() => onOpen(row)} aria-label={`Open ${row.name}`}>Open</Button></div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollEdges>
      )}
      {rows.length > SHOWN && <div className="dw-more"><Button onClick={() => setAll((v) => !v)}>{all ? 'Show fewer' : `Show all ${rows.length}`}</Button></div>}
    </section>
  )
}
