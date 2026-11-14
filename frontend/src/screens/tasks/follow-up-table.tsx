import { ArrowRight, UserRound } from 'lucide-react'
import { RecordIdentity } from '@/shared/ui/workspace'
import { Button } from '@/shared/ui/primitives'
import { cn } from '@/shared/lib/cn'
import { staleLabel, type FollowUpItem } from '@/features/daily-work/daily-work-model'
import { OwnerTag } from './task-identity'
import { AmountCell, CandidateIds, ClientCountryCell, columnLayout, OrderRefCell, PositionCellView, RemarkCell, ReviewCell, RowMenu, ScrollEdges, StageCell } from './daily-table'

/**
 * The same agreed column format and width plan as Today's candidates (the Recruiter column is for supervisors; the trailing
 * actions column has no visible title). Widths are px weights: see columnLayout in daily-table.tsx.
 */
const COLUMNS = [
  { key: 'no', title: 'S NO', width: 44 },
  { key: 'client', title: 'CLIENT / COUNTRY', width: 124, left: true },
  { key: 'candidate', title: 'CANDIDATE NAME', width: 164, left: true },
  { key: 'staff', title: 'Recruiter', width: 124, supervisor: true, left: true },
  { key: 'position', title: 'JOB', width: 100, left: true },
  { key: 'ref', title: 'REF', width: 124 },
  { key: 'stage', title: 'STAGE', width: 108 },
  { key: 'update', title: 'REMARKS', width: 136, left: true },
  { key: 'amount', title: 'AMOUNT', width: 96 },
  { key: 'review', title: 'GOOGLE REVIEW', width: 84 },
  { key: 'actions', title: 'Actions', width: 128, quiet: true },
] as const

export interface FollowUpTableProps {
  items: FollowUpItem[]
  now: number
  supervisor: boolean
  staffName: (id: string) => string | undefined
  canWork: (item: FollowUpItem) => boolean
  onOpen: (item: FollowUpItem) => void
  onStage: (item: FollowUpItem) => void
  onProfile: (item: FollowUpItem) => void
  onRemark: (item: FollowUpItem, text: string) => Promise<void>
}

/** The follow-up list in the same look as Today's candidates: the stalest candidate first, how long since the last update under the remark. */
export function FollowUpTable({ items, now, supervisor, staffName, canWork, onOpen, onStage, onProfile, onRemark }: FollowUpTableProps) {
  const columns = COLUMNS.filter((c) => supervisor || !('supervisor' in c))
  const layout = columnLayout(columns)
  return (
    <ScrollEdges label="Follow-up candidates, scroll sideways for more columns">
      <table className="dw-table dw-main dw-follow" aria-label="Follow-up candidates" style={{ minWidth: layout.minWidth }}>
        <colgroup>{columns.map((c) => <col key={c.key} style={{ width: layout.share(c) }} />)}</colgroup>
        <thead><tr>{columns.map((c) => <th key={c.key} scope="col" className={cn('left' in c && 'is-left', c.key === 'actions' && 'is-actions')}>{'quiet' in c ? <span className="sr-only">{c.title}</span> : c.title}</th>)}</tr></thead>
        <tbody>
          {items.map((item, index) => {
            const name = staffName(item.ownerId)
            return (
              <tr key={item.id} className={cn('dw-row', index % 2 === 1 && 'is-alt')}>
                <td className="is-num tnum" data-label="S NO">{index + 1}</td>
                <td data-label="CLIENT / COUNTRY" className="is-left"><ClientCountryCell item={{ ...item, kind: 'stage' }} onAdd={() => onProfile(item)} /></td>
                <td data-label="CANDIDATE NAME" className="is-left"><RecordIdentity name={item.candidate} detail={<CandidateIds item={item} />} /></td>
                {supervisor && <td data-label="Recruiter" className="is-left">{name ? <OwnerTag name={name} /> : <span className="dw-none">Not assigned</span>}</td>}
                <td data-label="JOB" className="is-left"><PositionCellView item={item} /></td>
                <td data-label="REF"><OrderRefCell item={item} /></td>
                <td data-label="STAGE"><StageCell item={item} /></td>
                <td data-label="REMARKS" className="is-left">
                  <small className="dw-stale" title={new Date(item.lastUpdateAt).toLocaleString()}>Last update {staleLabel(item.lastUpdateAt, now)}</small>
                  <RemarkCell item={item} canWork={canWork(item)} onSave={(text) => onRemark(item, text)} />
                </td>
                <td data-label="AMOUNT"><AmountCell item={item} /></td>
                <td data-label="GOOGLE REVIEW"><ReviewCell item={item} /></td>
                <td data-label="Actions" className="is-actions">
                  <div className="dw-actions">
                    <Button size="sm" variant="primary" className="dw-act" onClick={() => onOpen(item)} aria-label={`Open the application form for ${item.candidate}`}>Open</Button>
                    <RowMenu candidate={item.candidate} items={[
                      { label: 'Open stage', icon: ArrowRight, onSelect: () => onStage(item) },
                      { label: 'Open candidate profile', icon: UserRound, onSelect: () => onProfile(item) },
                    ]} />
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </ScrollEdges>
  )
}
