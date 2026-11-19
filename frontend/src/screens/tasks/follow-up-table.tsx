import { ArrowRight, Globe, UserRound } from 'lucide-react'
import { RecordIdentity } from '@/shared/ui/workspace'
import { Button } from '@/shared/ui/primitives'
import { cn } from '@/shared/lib/cn'
import { countryCode } from '@/shared/lib/country-flag'
import { advanceLabel, countryCell, staleLabel, type FollowUpItem } from '@/features/daily-work/daily-work-model'
import { OwnerTag } from './task-identity'
import { AmountCell, CandidateIds, ClientCell, columnLayout, OrderRefCell, PositionCellView, RemarkCell, ReviewCell, RowMenu, ScrollEdges, StageCell, TASK_COLUMNS } from './daily-table'

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

/** The follow-up list in the same columns and look as Today's candidates: the stalest candidate first, how long since the last update as the muted line over the remark field. */
export function FollowUpTable({ items, now, supervisor, staffName, canWork, onOpen, onStage, onProfile, onRemark }: FollowUpTableProps) {
  const columns = TASK_COLUMNS.filter((c) => supervisor || !('supervisor' in c))
  const layout = columnLayout(columns)
  return (
    <ScrollEdges label="Follow-up candidates, scroll sideways for more columns">
      <table className="dw-table dw-main dw-follow" aria-label="Follow-up candidates" style={{ minWidth: layout.minWidth }}>
        <colgroup>{columns.map((c) => <col key={c.key} style={{ width: layout.share(c) }} />)}</colgroup>
        <thead><tr>{columns.map((c) => <th key={c.key} scope="col" className={cn('left' in c && 'is-left', c.key === 'actions' && 'is-actions')}>{'quiet' in c ? <span className="sr-only">{c.title}</span> : c.title}</th>)}</tr></thead>
        <tbody>
          {items.map((item, index) => {
            const name = staffName(item.ownerId)
            const noCountry = countryCell(item, countryCode).names.length === 0
            return (
              <tr key={item.id} className={cn('dw-row', index % 2 === 1 && 'is-alt')}>
                <td className="is-num tnum" data-label="S NO">{index + 1}</td>
                <td data-label="CLIENT" className={cn('is-left', !item.client && 'is-empty')}><ClientCell item={{ ...item, kind: 'stage' }} /></td>
                <td data-label="CANDIDATE NAME" className="is-left"><RecordIdentity name={item.candidate} detail={<CandidateIds item={item} />} /></td>
                {supervisor && <td data-label="Recruiter" className="is-left">{name ? <OwnerTag name={name} /> : <span className="dw-none">—</span>}</td>}
                <td data-label="JOB" className={cn('is-left', !item.position && 'is-empty')}><PositionCellView item={item} /></td>
                <td data-label="REF" className={cn(!item.orderRef && 'is-empty')}><OrderRefCell item={item} /></td>
                <td data-label="STAGE"><StageCell item={item} /></td>
                <td data-label="REMARKS" className="is-left">
                  <RemarkCell item={item} canWork={canWork(item)} onSave={(text) => onRemark(item, text)} lead={`Last update ${staleLabel(item.lastUpdateAt, now)}`} />
                </td>
                <td data-label="AMOUNT" className={cn(!advanceLabel(item) && 'is-empty')}><AmountCell item={item} /></td>
                <td data-label="GOOGLE REVIEW" className={cn(!item.googleReview && 'is-empty')}><ReviewCell item={item} /></td>
                <td data-label="Actions" className="is-actions">
                  <div className="dw-actions">
                    <Button size="sm" variant="primary" className="dw-act" onClick={() => onOpen(item)} aria-label={`Open the application form for ${item.candidate}`}>Open</Button>
                    <RowMenu candidate={item.candidate} items={[
                      { label: 'Open stage', icon: ArrowRight, onSelect: () => onStage(item) },
                      { label: 'Open candidate profile', icon: UserRound, onSelect: () => onProfile(item) },
                      ...(noCountry ? [{ label: 'Add preferred country', icon: Globe, onSelect: () => onProfile(item) }] : []),
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
