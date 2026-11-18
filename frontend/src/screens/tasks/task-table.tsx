import { ArrowRight, Globe, UserRound } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { countryCode } from '@/shared/lib/country-flag'
import { Button } from '@/shared/ui/primitives'
import { RecordIdentity } from '@/shared/ui/workspace'
import { clockTime, countryCell, rowAction, rowTarget, type TaskRow } from '@/features/daily-work/daily-work-model'
import { OwnerTag } from './task-identity'
import {
  CandidateIds, ClientCountryCell, PositionCellView, RemarkCell, RowMenu, ScrollEdges, StageCell, columnLayout,
} from './daily-table'

/** One list for every recruiter's candidates, with what happened to each of them today. */
const COLUMNS = [
  { key: 'no', title: 'S NO', width: 40 },
  { key: 'client', title: 'CLIENT / COUNTRY', width: 124, left: true },
  { key: 'candidate', title: 'CANDIDATE NAME', width: 160, left: true },
  { key: 'staff', title: 'Recruiter', width: 100, supervisor: true, left: true },
  { key: 'position', title: 'JOB', width: 120, left: true },
  { key: 'stage', title: 'STAGE', width: 156 },
  { key: 'today', title: 'TODAY', width: 130, left: true },
  { key: 'remarks', title: 'REMARKS', width: 176, left: true },
  { key: 'actions', title: 'Actions', width: 160, quiet: true },
] as const

export interface TaskTableProps {
  rows: TaskRow[]
  zone?: string
  supervisor: boolean
  staffName: (id: string) => string | undefined
  canWork: (row: TaskRow) => boolean
  onOpen: (row: TaskRow) => void
  onProfile: (row: TaskRow) => void
  onRemark: (row: TaskRow, text: string) => Promise<void>
}

export function TaskTable({ rows, zone, supervisor, staffName, canWork, onOpen, onProfile, onRemark }: TaskTableProps) {
  const columns = COLUMNS.filter((c) => supervisor || !('supervisor' in c))
  const layout = columnLayout(columns)
  return (
    <ScrollEdges label="Candidates, scroll sideways for more columns">
      <table className="dw-table dw-main tw-table" aria-label="Candidates" style={{ minWidth: layout.minWidth }}>
        <colgroup>{columns.map((c) => <col key={c.key} style={{ width: layout.share(c) }} />)}</colgroup>
        <thead><tr>{columns.map((c) => <th key={c.key} scope="col" className={cn('left' in c && 'is-left', c.key === 'actions' && 'is-actions')}>{'quiet' in c ? <span className="sr-only">{c.title}</span> : c.title}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, index) => {
            const name = staffName(row.ownerId)
            const action = rowAction(row)
            const noCountry = countryCell(row, countryCode).names.length === 0
            const open = rowTarget(row) === 'profile' ? () => onProfile(row) : () => onOpen(row)
            const { called, moved, remarked } = row.today
            return (
              <tr key={row.candidateId} className={cn('dw-row', index % 2 === 1 && 'is-alt')}>
                <td className="is-num tnum" data-label="S NO">{index + 1}</td>
                <td data-label="CLIENT / COUNTRY" className={cn('is-left', !row.client && noCountry && 'is-empty')}><ClientCountryCell item={row} /></td>
                <td data-label="CANDIDATE NAME" className="is-left"><RecordIdentity name={row.candidate} detail={<CandidateIds item={row} />} /></td>
                {supervisor && <td data-label="Recruiter" className="is-left">{name ? <OwnerTag name={name} /> : <span className="dw-none">—</span>}</td>}
                <td data-label="JOB" className={cn('is-left', !row.position && 'is-empty')}><PositionCellView item={row} /></td>
                <td data-label="STAGE"><StageCell item={row} /></td>
                <td data-label="TODAY" className="is-left">
                  <span className="tw-today">
                    {called && <span className="tw-chip">Called{row.completedAt ? ` ${clockTime(row.completedAt, zone)}` : ''}</span>}
                    {moved && <span className="tw-chip">Stage moved{row.completedAt ? ` ${clockTime(row.completedAt, zone)}` : ''}</span>}
                    {remarked && <span className="tw-chip is-remark">Remark added</span>}
                    {!called && !moved && !remarked && <span className="tw-chip is-none">Nothing yet</span>}
                  </span>
                </td>
                <td data-label="REMARKS" className={cn('is-left', !row.remark && !canWork(row) && 'is-empty')}><RemarkCell item={row} canWork={canWork(row)} onSave={(text) => onRemark(row, text)} /></td>
                <td data-label="Actions" className="is-actions">
                  <div className="dw-actions">
                    <Button size="sm" variant={called || moved ? 'secondary' : 'primary'} className="dw-act" onClick={open}
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
