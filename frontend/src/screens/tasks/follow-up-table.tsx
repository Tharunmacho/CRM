import { ArrowRight, UserRound } from 'lucide-react'
import { RecordIdentity } from '@/shared/ui/workspace'
import { Button } from '@/shared/ui/primitives'
import { cn } from '@/shared/lib/cn'
import { staleLabel, type FollowUpItem } from '@/features/daily-work/daily-work-model'
import { OwnerTag } from './task-identity'
import { CandidateIds, ClientCell, CountryCell, PositionCellView, RemarkCell, RowMenu, ScrollEdges, StageCell } from './daily-table'

/** Column order and pixel widths (the table scrolls sideways below their sum; the recruiter column is for supervisors). */
const COLUMNS = [
  { key: 'no', title: 'S.No', width: 44 },
  { key: 'client', title: 'Client', width: 98 },
  { key: 'country', title: 'Country', width: 108 },
  { key: 'candidate', title: 'Candidate', width: 156, left: true },
  { key: 'position', title: 'Job', width: 92 },
  { key: 'staff', title: 'Recruiter', width: 96, supervisor: true },
  { key: 'stage', title: 'Stage', width: 124 },
  { key: 'update', title: 'Last update / remarks', width: 190, left: true },
  { key: 'actions', title: 'Actions', width: 128 },
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
  return (
    <ScrollEdges label="Follow-up candidates, scroll sideways for more columns">
      <table className="dw-table dw-main" aria-label="Follow-up candidates" style={{ minWidth: columns.reduce((sum, c) => sum + c.width, 0) }}>
        <colgroup>{columns.map((c) => <col key={c.key} style={{ width: c.width }} />)}</colgroup>
        <thead><tr>{columns.map((c) => <th key={c.key} scope="col" className={cn('left' in c && 'is-left')}>{c.title}</th>)}</tr></thead>
        <tbody>
          {items.map((item, index) => {
            const name = staffName(item.ownerId)
            return (
              <tr key={item.id} className={cn('dw-row', index % 2 === 1 && 'is-alt')}>
                <td className="is-num tnum" data-label="S.No">{index + 1}</td>
                <td data-label="Client"><ClientCell item={{ ...item, kind: 'stage' }} /></td>
                <td data-label="Country"><CountryCell item={item} onAdd={() => onProfile(item)} /></td>
                <td data-label="Candidate" className="is-left"><RecordIdentity name={item.candidate} detail={<CandidateIds item={item} />} /></td>
                <td data-label="Job"><PositionCellView item={item} /></td>
                {supervisor && <td data-label="Recruiter">{name ? <OwnerTag name={name} /> : <span className="dw-none">Not assigned</span>}</td>}
                <td data-label="Stage"><StageCell item={item} /></td>
                <td data-label="Last update / remarks" className="is-left">
                  <small className="dw-stale" title={new Date(item.lastUpdateAt).toLocaleString()}>Last update {staleLabel(item.lastUpdateAt, now)}</small>
                  <RemarkCell item={item} canWork={canWork(item)} onSave={(text) => onRemark(item, text)} />
                </td>
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
