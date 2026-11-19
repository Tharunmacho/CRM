import { ArrowRight, Globe, UserRound } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { countryCode } from '@/shared/lib/country-flag'
import { Button } from '@/shared/ui/primitives'
import { RecordIdentity } from '@/shared/ui/workspace'
import { agencyLabel, countryCell, nothingYet, rowAction, taskColumnsFor, todayChips, type TaskRow } from '@/features/daily-work/daily-work-model'
import { OwnerTag } from './task-identity'
import {
  AgencyCell, ClientCell, DocCell, PositionCellView, RemarkCell, RowMenu, ScrollEdges, StageCell, columnLayout,
} from './daily-table'

/**
 * One list for every recruiter's candidates, with what happened to each of them today. The columns and their widths are
 * TASK_LIST_COLUMNS in the daily-work model (S NO, DOC, CLIENT, JOB, CANDIDATE, Recruiter, AGENCY, STAGE, TODAY, REMARKS, actions): the widths add
 * up to 1108px for a supervisor (1008px for a recruiter) and are used as percentages, so the table always fills its panel (1111px at a 1440px
 * window with the sidebar open) and never scrolls sideways; under 1080px of panel width the list is a stack of cards instead. Text wraps to two
 * lines (the full text is the tooltip) instead of being cut with an ellipsis. The look is src/styles/tasks-table.css.
 */
export interface TaskTableProps {
  rows: TaskRow[]
  zone?: string
  supervisor: boolean
  staffName: (id: string) => string | undefined
  canWork: (row: TaskRow) => boolean
  onOpen: (row: TaskRow) => void
  onProfile: (row: TaskRow) => void
  /** The candidate's application form on its Overview tab, where the date of commencement is filled in. */
  onAddDoc: (row: TaskRow) => void
  onRemark: (row: TaskRow, text: string) => Promise<void>
}

export function TaskTable({ rows, zone, supervisor, staffName, canWork, onOpen, onProfile, onAddDoc, onRemark }: TaskTableProps) {
  const columns = taskColumnsFor(supervisor)
  const layout = columnLayout(columns)
  return (
    <ScrollEdges label="Candidates, scroll sideways for more columns">
      <table className="dw-table dw-main tw-table" aria-label="Candidates">
        <colgroup>{columns.map((c) => <col key={c.key} style={{ width: layout.share(c) }} />)}</colgroup>
        <thead><tr>{columns.map((c) => <th key={c.key} scope="col" className={cn(c.left && 'is-left', c.key === 'actions' && 'is-actions')}>{c.quiet ? <span className="sr-only">{c.title}</span> : c.title}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, index) => {
            const name = staffName(row.ownerId)
            const action = rowAction(row)
            const noCountry = countryCell(row, countryCode).names.length === 0
            const { called, moved, remarked } = row.today
            const chips = todayChips(row, zone)
            const work = canWork(row)
            return (
              <tr key={row.candidateId} className="dw-row">
                <td className="is-num tnum" data-label="S NO">{index + 1}</td>
                <td data-label="DOC" className={cn(!row.commencementDate && !(work && row.applicationId) && 'is-empty')}><DocCell item={row} canAdd={work} onAdd={() => onAddDoc(row)} /></td>
                <td data-label="CLIENT" className={cn('is-left', !row.client && 'is-empty')}><ClientCell item={row} /></td>
                <td data-label="JOB" className={cn('is-left', !row.position && 'is-empty')}><PositionCellView item={row} /></td>
                <td data-label="CANDIDATE NAME" className="is-left"><RecordIdentity name={row.candidate} /></td>
                {supervisor && <td data-label="Recruiter" className="is-left">{name ? <OwnerTag name={name} /> : <span className="dw-none">—</span>}</td>}
                <td data-label="AGENCY" className={cn('is-left', !agencyLabel(row) && 'is-empty')}><AgencyCell item={row} /></td>
                <td data-label="STAGE"><StageCell item={row} /></td>
                <td data-label="TODAY" className="is-left">
                  <span className="tw-today">
                    {chips.map((chip) => <span key={chip.key} className="tw-chip" title={chip.text}>{chip.key === 'moved' ? chip.text.replace(/^Stage moved/, 'Moved') : chip.text}</span>)}
                    {nothingYet(row.today) && <span className="tw-chip is-none" title="Nothing was done for this candidate today">Nothing yet</span>}
                    {remarked && chips.length === 0 && <span className="dw-none" title="A remark was written today">—</span>}
                  </span>
                </td>
                <td data-label="REMARKS" className={cn('is-left', !row.remark && !work && 'is-empty')}><RemarkCell item={row} canWork={work} onSave={(text) => onRemark(row, text)} /></td>
                <td data-label="Actions" className="is-actions">
                  <div className="dw-actions">
                    <Button size="sm" variant={called || moved ? 'secondary' : 'primary'} className="dw-act" onClick={() => onOpen(row)}
                      aria-label={`${action === 'Complete' ? 'Complete the stage for' : action} ${row.candidate}`}>{action}</Button>
                    <RowMenu candidate={row.candidate} items={[
                      ...(row.applicationId ? [{ label: moved ? 'View stage' : 'Open stage', icon: ArrowRight, onSelect: () => onOpen(row) }] : []),
                      { label: 'Open candidate profile', icon: UserRound, onSelect: () => onProfile(row) },
                      ...(noCountry ? [{ label: 'Add preferred country', icon: Globe, onSelect: () => onProfile(row) }] : []),
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
