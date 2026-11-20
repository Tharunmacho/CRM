import { ArrowRight, Globe, SendHorizontal, Trash2, UserRound } from 'lucide-react'
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { cn } from '@/shared/lib/cn'
import { countryCode } from '@/shared/lib/country-flag'
import { Button } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { useAutoGrow } from '@/shared/ui/use-auto-grow'
import { RecordIdentity } from '@/shared/ui/workspace'
import { agencyLabel, clockTime, countryCell, isClosed, nothingYet, noteWhen, notesNewestFirst, PENDING_NOTE, rowAction, sendNote, taskColumnsFor, todayChips, todayPlace, type DailyNote, type TaskRow } from '@/features/daily-work/daily-work-model'
import { DELETE_NOTE_HINT, DELETE_NOTE_LABEL, DELETE_NOTE_TITLE } from '@/features/daily-work/note-delete-model'
import { OwnerTag } from './task-identity'
import {
  AgencyCell, ClientCell, DocCell, PositionCellView, RemarkCell, RowMenu, ScrollEdges, StageCell, columnLayout,
} from './daily-table'

/**
 * One list for every recruiter's candidates, with what happened to each of them today. The columns and their widths are
 * TASK_LIST_COLUMNS in the daily-work model (S NO, DOC, CLIENT, JOB, CANDIDATE, Recruiter, AGENCY, STAGE, TODAY, REMARKS, NOTES, actions): the widths add
 * up to 1108px for a supervisor (1021px for a recruiter) without TODAY and are used as percentages, so the table always fills its panel (1111px at a 1440px
 * window with the sidebar open) and never scrolls sideways; under 1080px of panel width the list is a stack of cards instead. Headers and cells are
 * centred; text wraps (the full text is the tooltip) instead of being cut with an ellipsis. The rows arrive sorted by DOC (taskRows). A candidate
 * whose placement ended (Declined, Rejected, Travelled) shows its outcome in STAGE, a dash in TODAY and REMARKS, a read-only DOC and only View.
 * TODAY is a column of its own only when the panel holds the whole plan (`todayPlace`); on a narrower table its chip sits in the STAGE cell, under the
 * stage chip and its dashes, and a phone card has a TODAY row.
 * NOTES is a column of its own at every width and REMARKS holds only the select (a remark and a note are different things: the remark is the candidate's
 * status line, notes are free text kept as a list): the NOTES cell has an always-visible input at the top (Enter saves, Shift+Enter is a new line, Escape clears;
 * only for people who may work the candidate) and under it ALL the candidate's notes, newest on top, each with its time ("11:40", or "18 Nov 09:15" on an earlier
 * day), its author and its text; the list is at most 168px tall and scrolls inside the cell. In the cards NOTES is a row of its own.
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
  /** A note typed in the NOTES cell (kind "note": never the remark). Rejects with the server's message. Without it no cell has an input. */
  onAddNote?: (row: TaskRow, text: string) => Promise<void>
  /** The signed-in person's name, shown on a note until the server has answered. */
  authorName?: string
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

interface NotesCellProps {
  row: TaskRow; zone?: string; canAdd: boolean; authorName?: string
  onAdd?: (row: TaskRow, text: string) => Promise<void>
  mayDelete: (row: TaskRow, note: DailyNote) => boolean
  onAsk: (row: TaskRow, note: DailyNote) => () => void
}

/** The NOTES cell: the input (people who may work the candidate), then every note newest first in a list that scrolls inside the cell. */
function NotesCell({ row, zone, canAdd, authorName, onAdd, mayDelete, onAsk }: NotesCellProps) {
  const [text, setText] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [pending, setPending] = useState<DailyNote[]>([])
  const box = useRef<HTMLTextAreaElement>(null)
  const typing = canAdd && !!onAdd
  useAutoGrow(box, text, { minRows: 1, maxRows: 3, enabled: typing })
  const notes = [...pending, ...notesNewestFirst(row.notes ?? row.todayNotes)]
  const submit = async () => {
    if (!onAdd || saving) return
    const typed = text
    if (!typed.trim()) return
    setSaving(true); setError(''); setText('')
    const result = await sendNote(typed, (body) => onAdd(row, body), {
      pending: (note) => setPending((list) => [note, ...list]),
      settled: (note) => setPending((list) => list.filter((item) => item.id !== note.id)),
    }, authorName)
    if (result.error) { setError(result.error); setText(result.body) }
    setSaving(false)
    if (result.error) window.setTimeout(() => box.current?.focus(), 0)
  }
  const onKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void submit() }
    else if (event.key === 'Escape' && text) { event.preventDefault(); event.stopPropagation(); setText(''); setError('') }
  }
  return (
    <div className="tw-notes-cell">
      {typing && (
        <div className="tw-note-add">
          <textarea ref={box} className="tw-note-input" rows={1} value={text} placeholder="Add a note…" maxLength={10000} disabled={saving}
            aria-label={`Add a note for ${row.candidate}`} aria-invalid={error ? true : undefined} onChange={(event) => { setText(event.target.value); if (error) setError('') }} onKeyDown={onKey} />
          <button type="button" className="tw-note-send" aria-label={`Save note for ${row.candidate}`} title="Save note (Enter)" disabled={saving || !text.trim()} onClick={() => void submit()}>
            <SendHorizontal size={14} aria-hidden="true" />
          </button>
        </div>
      )}
      {error && <p className="tw-note-error" role="alert">{error}</p>}
      {notes.length > 0 ? (
        <ul className="tw-note-list" aria-label={`Notes of ${row.candidate}, newest first`} tabIndex={notes.length > 2 ? 0 : undefined}>
          {notes.map((note) => (
            <li key={note.id} className={cn(note.id.startsWith(PENDING_NOTE) && 'is-pending')}>
              <div className="tw-note-head">
                <span className="tw-note-when"><time dateTime={note.at}>{noteWhen(note.at, zone)}</time><small>{note.by}</small></span>
                {!note.id.startsWith(PENDING_NOTE) && mayDelete(row, note) && <NoteDeleteButton note={note} zone={zone} onAsk={onAsk(row, note)} />}
              </div>
              <p className="tw-note-body">{note.body}</p>
            </li>
          ))}
        </ul>
      ) : !typing && <span className="dw-none" title="No notes yet">&mdash;</span>}
    </div>
  )
}

/** The TODAY chips of a candidate: what was done today (called, stage moved, a note), or "Nothing yet". */
function TodayChips({ row, zone, inStage }: { row: TaskRow; zone?: string; inStage?: boolean }) {
  const chips = todayChips(row, zone)
  const noteTime = notesNewestFirst(row.todayNotes)[0]?.at
  return (
    <span className={cn('tw-today', inStage && 'is-in-stage')}>
      {chips.map((chip) => <span key={chip.key} className="tw-chip" title={chip.text}>{chip.key === 'moved' ? chip.text.replace(/^Stage moved/, 'Moved') : chip.text}</span>)}
      {nothingYet(row.today) && <span className="tw-chip is-none" title="Nothing was done for this candidate today">Nothing yet</span>}
      {row.today.remarked && chips.length === 0 && (noteTime
        ? <span className="tw-chip" title="Latest note today">Note {clockTime(noteTime, zone)}</span>
        : <span className="tw-chip" title="Remark set today">Remark{row.remark?.at && ` ${clockTime(row.remark.at, zone)}`}</span>)}
    </span>
  )
}

export function TaskTable({ rows, zone, supervisor, staffName, canWork, onOpen, onProfile, onSaveDoc, onRemark, onClearRemark, onAddNote, authorName, canDeleteNote, onDeleteNote, labelledBy, busy }: TaskTableProps) {
  const [measure, panelWidth] = useTableWidth()
  const [asking, setAsking] = useState<{ row: TaskRow; note: DailyNote } | null>(null)
  const mayDelete = (row: TaskRow, note: DailyNote) => !!onDeleteNote && !!canDeleteNote?.(row, note)
  const ask = (row: TaskRow, note: DailyNote) => () => setAsking({ row, note })
  const place = todayPlace(supervisor, panelWidth)
  const columns = taskColumnsFor(supervisor, place !== 'stage')
  const layout = columnLayout(columns)
  return (
    <ScrollEdges label="Candidates, scroll sideways for more columns" labelledBy={labelledBy} busy={busy}>
      <table ref={measure} className="dw-table dw-main tw-table" aria-label="Candidates">
        <colgroup>{columns.map((c) => <col key={c.key} style={{ width: layout.share(c) }} />)}</colgroup>
        <thead><tr>{columns.map((c) => <th key={c.key} scope="col" className={cn(c.key === 'actions' && 'is-actions')}>{c.quiet ? <span className="sr-only">{c.title}</span> : c.title}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, index) => {
            const name = staffName(row.ownerId)
            const action = rowAction(row)
            const closed = isClosed(row)
            const noCountry = countryCell(row, countryCode).names.length === 0
            const { called, moved } = row.today
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
                <td data-label="STAGE"><StageCell item={row} zone={zone} />{place === 'stage' && !closed && <TodayChips row={row} zone={zone} inStage />}</td>
                {place !== 'stage' && (
                  <td data-label="TODAY" className={cn(closed && 'is-empty')}>
                    {closed ? <span className="dw-none" title="Nothing is worked on a candidate whose placement ended">—</span> : <TodayChips row={row} zone={zone} />}
                  </td>
                )}
                <td data-label="REMARKS" className={cn((closed || (!row.remark && !work)) && 'is-empty')}>
                  {closed ? <span className="dw-none" title="Nothing is worked on a candidate whose placement ended">—</span> : <RemarkCell item={row} canWork={work} onSave={(text) => onRemark(row, text)} onClear={onClearRemark && row.taskId ? () => onClearRemark(row) : undefined} />}
                </td>
                <td data-label="NOTES" className={cn(!work && !(row.notes ?? row.todayNotes)?.length && 'is-empty')}>
                  <NotesCell row={row} zone={zone} canAdd={work && !!row.taskId} authorName={authorName} onAdd={onAddNote} mayDelete={mayDelete} onAsk={ask} />
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
