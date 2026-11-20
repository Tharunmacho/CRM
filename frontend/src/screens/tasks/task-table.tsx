import { ArrowRight, Globe, Trash2, UserRound } from 'lucide-react'
import { useEffect, useState } from 'react'
import { cn } from '@/shared/lib/cn'
import { countryCode } from '@/shared/lib/country-flag'
import { Button } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { RecordIdentity } from '@/shared/ui/workspace'
import { agencyLabel, clockTime, countryCell, isClosed, nothingYet, rowAction, showsNotesColumn, taskColumnsFor, todayChips, type DailyNote, type TaskRow } from '@/features/daily-work/daily-work-model'
import { DELETE_NOTE_HINT, DELETE_NOTE_LABEL, DELETE_NOTE_TITLE } from '@/features/daily-work/note-delete-model'
import { OwnerTag } from './task-identity'
import {
  AgencyCell, ClientCell, DocCell, PositionCellView, RemarkCell, RowMenu, ScrollEdges, StageCell, columnLayout,
} from './daily-table'

/**
 * One list for every recruiter's candidates, with what happened to each of them today. The columns and their widths are
 * TASK_LIST_COLUMNS in the daily-work model (S NO, DOC, CLIENT, JOB, CANDIDATE, Recruiter, AGENCY, STAGE, TODAY, REMARKS, NOTES, actions): the widths add
 * up to 1108px for a supervisor (1017px for a recruiter) and are used as percentages, so the table always fills its panel (1111px at a 1440px
 * window with the sidebar open) and never scrolls sideways; under 1080px of panel width the list is a stack of cards instead. Headers and cells are
 * centred; text wraps (the full text is the tooltip) instead of being cut with an ellipsis. The rows arrive sorted by DOC (taskRows). A candidate
 * whose placement ended (Declined, Rejected, Travelled) shows its outcome in STAGE, a dash in TODAY and REMARKS, a read-only DOC and only View.
 * NOTES (today's notes of the candidate, newest first) is a column only on a panel wide enough for it (`showsNotesColumn`); on a narrower table
 * its cells are hidden by CSS and the notes show as a small second line under the remark, and in the cards as a row of their own.
 * Every note has a small "Delete note" button for the people who may delete it (`canDeleteNote`: the note's author, the candidate's recruiter, Admin and
 * MD; the server enforces the same rule); it asks first in a small dialog, then `onDeleteNote` hides the note at once (the screen restores it and says so
 * when the server refuses). The Remarks dropdown's first entry, "Select remark", empties the cell through `onClearRemark`.
 * The look is src/styles/tasks-table.css.
 */
export interface TaskTableProps {
  rows: TaskRow[]
  zone?: string
  supervisor: boolean
  staffName: (id: string) => string | undefined
  canWork: (row: TaskRow) => boolean
  onOpen: (row: TaskRow) => void
  onProfile: (row: TaskRow) => void
  /** Save the date of commencement typed in the DOC cell (null clears it). */
  onSaveDoc: (row: TaskRow, iso: string | null) => Promise<void>
  onRemark: (row: TaskRow, text: string) => Promise<void>
  /** "Select remark": the candidate's remark cell reads empty again (the notes and the history stay). */
  onClearRemark?: (row: TaskRow) => Promise<void>
  /** Whether this person may delete this note; without it no note has a delete button. */
  canDeleteNote?: (row: TaskRow, note: DailyNote) => boolean
  /** Delete the note: the screen hides it at once and reports a failure itself. */
  onDeleteNote?: (row: TaskRow, note: DailyNote) => void
  /** The filter-bar tab this table belongs to, and whether the next view is still loading (the rows are then the previous ones). */
  labelledBy?: string
  busy?: boolean
}

/** The table's own width (it is as wide as the panel), kept current. 0 until measured. */
function useTableWidth() {
  const [element, setElement] = useState<HTMLTableElement | null>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    if (!element) return
    const read = () => setWidth(Math.round(element.getBoundingClientRect().width))
    read()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(read)
    observer.observe(element)
    return () => observer.disconnect()
  }, [element])
  return [setElement, width] as const
}

const noteLine = (note: { at: string; by: string; body: string }, zone?: string) => `${clockTime(note.at, zone)} ${note.by} - ${note.body}`

/** The small trash button of one note. Same label for everyone; the time and author are in the tooltip. */
function NoteDeleteButton({ note, zone, onAsk }: { note: DailyNote; zone?: string; onAsk: () => void }) {
  return (
    <button type="button" className="tw-note-del" aria-label={DELETE_NOTE_LABEL} title={`${DELETE_NOTE_LABEL}: ${noteLine(note, zone)}`} onClick={onAsk}>
      <Trash2 size={13} aria-hidden="true" />
    </button>
  )
}

export function TaskTable({ rows, zone, supervisor, staffName, canWork, onOpen, onProfile, onSaveDoc, onRemark, onClearRemark, canDeleteNote, onDeleteNote, labelledBy, busy }: TaskTableProps) {
  const [measure, panelWidth] = useTableWidth()
  const [asking, setAsking] = useState<{ row: TaskRow; note: DailyNote } | null>(null)
  const mayDelete = (row: TaskRow, note: DailyNote) => !!onDeleteNote && !!canDeleteNote?.(row, note)
  const ask = (row: TaskRow, note: DailyNote) => () => setAsking({ row, note })
  const wide = showsNotesColumn(supervisor, panelWidth)
  const columns = taskColumnsFor(supervisor, wide)
  const layout = columnLayout(columns)
  return (
    <ScrollEdges label="Candidates, scroll sideways for more columns" labelledBy={labelledBy} busy={busy}>
      <table ref={measure} className={cn('dw-table dw-main tw-table', wide && 'has-notes')} aria-label="Candidates">
        <colgroup>{columns.map((c) => <col key={c.key} style={{ width: layout.share(c) }} />)}</colgroup>
        <thead><tr>{columns.map((c) => <th key={c.key} scope="col" className={cn(c.key === 'actions' && 'is-actions')}>{c.quiet ? <span className="sr-only">{c.title}</span> : c.title}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, index) => {
            const name = staffName(row.ownerId)
            const action = rowAction(row)
            const closed = isClosed(row)
            const noCountry = countryCell(row, countryCode).names.length === 0
            const { called, moved, remarked } = row.today
            const chips = todayChips(row, zone)
            const notes = row.todayNotes ?? []
            const noteTime = row.todayNotes?.[0]?.at ?? row.remark?.at
            const work = canWork(row) && !closed
            return (
              <tr key={row.candidateId} className={cn('dw-row', closed && 'is-closed')}>
                <td className="is-num tnum" data-label="S NO">{index + 1}</td>
                <td data-label="DOC" className={cn(!row.commencementDate && !(work && row.applicationId) && 'is-empty')}><DocCell item={row} canAdd={work} onSave={(iso) => onSaveDoc(row, iso)} /></td>
                <td data-label="CLIENT" className={cn(!row.client && 'is-empty')}><ClientCell item={row} country /></td>
                <td data-label="JOB" className={cn(!row.position && 'is-empty')}><PositionCellView item={row} /></td>
                <td data-label="CANDIDATE NAME"><RecordIdentity name={row.candidate} /></td>
                {supervisor && <td data-label="Recruiter">{name ? <OwnerTag name={name} /> : <span className="dw-none">—</span>}</td>}
                <td data-label="AGENCY" className={cn(!agencyLabel(row) && 'is-empty')}><AgencyCell item={row} /></td>
                <td data-label="STAGE"><StageCell item={row} zone={zone} /></td>
                <td data-label="TODAY" className={cn(closed && 'is-empty')}>
                  {closed ? <span className="dw-none" title="Nothing is worked on a candidate whose placement ended">—</span> : (
                    <span className="tw-today">
                      {chips.map((chip) => <span key={chip.key} className="tw-chip" title={chip.text}>{chip.key === 'moved' ? chip.text.replace(/^Stage moved/, 'Moved') : chip.text}</span>)}
                      {nothingYet(row.today) && <span className="tw-chip is-none" title="Nothing was done for this candidate today">Nothing yet</span>}
                      {remarked && chips.length === 0 && <span className="tw-chip" title="Latest note today">Note{noteTime && ` ${clockTime(noteTime, zone)}`}</span>}
                    </span>
                  )}
                </td>
                <td data-label="REMARKS" className={cn((closed || (!row.remark && !work)) && 'is-empty')}>
                  {closed ? <span className="dw-none" title="Nothing is worked on a candidate whose placement ended">—</span> : <RemarkCell item={row} canWork={work} onSave={(text) => onRemark(row, text)} onClear={onClearRemark && row.taskId ? () => onClearRemark(row) : undefined} />}
                  {!closed && notes.length > 0 && (
                    <span className="tw-remark-notes" title={notes.map((note) => noteLine(note, zone)).join('\n')}>
                      <span className="tw-remark-note-text"><time dateTime={notes[0].at}>{clockTime(notes[0].at, zone)}</time> {notes[0].body}{notes.length > 1 && ` (+${notes.length - 1} more)`}</span>
                      {mayDelete(row, notes[0]) && <NoteDeleteButton note={notes[0]} zone={zone} onAsk={ask(row, notes[0])} />}
                    </span>
                  )}
                </td>
                <td data-label="NOTES" className={cn(!notes.length && 'is-empty')}>
                  {notes.length ? (
                    <ul className="tw-notes" aria-label="Today's notes">
                      {notes.map((note) => (
                        <li key={note.id}>
                          <small><time dateTime={note.at}>{clockTime(note.at, zone)}</time> &middot; {note.by}</small>
                          <span className="tw-note-body">{note.body}</span>
                          {mayDelete(row, note) && <NoteDeleteButton note={note} zone={zone} onAsk={ask(row, note)} />}
                        </li>
                      ))}
                    </ul>
                  ) : <span className="dw-none" title="No notes today">&mdash;</span>}
                </td>
                <td data-label="Actions" className="is-actions">
                  <div className="dw-actions">
                    <Button size="sm" variant={called || moved || closed ? 'secondary' : 'primary'} className="dw-act" onClick={() => onOpen(row)}
                      aria-label={`${action === 'Complete' ? 'Complete the stage for' : action} ${row.candidate}`}>{action}</Button>
                    <RowMenu candidate={row.candidate} items={[
                      ...(row.applicationId ? [{ label: moved || closed ? 'View stage' : 'Open stage', icon: ArrowRight, onSelect: () => onOpen(row) }] : []),
                      { label: 'Open candidate profile', icon: UserRound, onSelect: () => onProfile(row) },
                      ...(noCountry && !closed ? [{ label: 'Add preferred country', icon: Globe, onSelect: () => onProfile(row) }] : []),
                    ]} />
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <Dialog open={!!asking} onClose={() => setAsking(null)} title={DELETE_NOTE_TITLE} description={DELETE_NOTE_HINT} width="sm"
        footer={<>
          <Button onClick={() => setAsking(null)}>Keep note</Button>
          <Button variant="danger" onClick={() => { const target = asking; setAsking(null); if (target) onDeleteNote?.(target.row, target.note) }}>
            <Trash2 size={15} aria-hidden="true" />{DELETE_NOTE_LABEL}
          </Button>
        </>}>
        {asking && <blockquote className="tw-note-quote"><small>{clockTime(asking.note.at, zone)} &middot; {asking.note.by} &middot; {asking.row.candidate}</small><p>{asking.note.body}</p></blockquote>}
      </Dialog>
    </ScrollEdges>
  )
}
