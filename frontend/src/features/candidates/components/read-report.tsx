import { useState } from 'react'
import { AlertTriangle, ChevronDown, RotateCw, ScrollText } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import type { ReadReportView } from '../read-report'
import '@/styles/add-candidate-v2.css'

type Notes = ReadReportView['notes']

/** The plain notes of one reading, one per line. A note about unreadable pages carries a Read again button when the host can offer it. */
export function ReadNotes({ notes, onRetry, busy = false }: { notes: Notes; onRetry?: () => void; busy?: boolean }) {
  return (
    <ul className="rr-notes">
      {notes.map((note) => (
        <li key={note.key}>
          <AlertTriangle size={14} aria-hidden />
          <span>{note.text}</span>
          {note.retry && onRetry && (
            <button type="button" className="rr-retry" disabled={busy} onClick={onRetry}><RotateCw size={13} aria-hidden />Read again</button>
          )}
        </li>
      ))}
    </ul>
  )
}

/** The "Log" button: closed by default, so nothing about the reading shows unless the recruiter asks. */
export function LogToggle({ open, count, onToggle, controls }: { open: boolean; count?: number; onToggle: () => void; controls: string }) {
  return (
    <button type="button" className={cn('rr-log-btn', open && 'is-open')} aria-expanded={open} aria-controls={controls} onClick={onToggle}>
      <ScrollText size={15} aria-hidden />Log
      {count ? <span className="rr-log-count tnum">{count}</span> : null}
      <ChevronDown size={14} className="rr-log-chev" aria-hidden />
    </button>
  )
}

/**
 * The reading log of one file (Documents block of the application form): nothing but a Log button until it is opened, and no
 * button at all when there is nothing to look at. Pure display: the host passes the built report and, when it can read a file
 * again, `onRetry`.
 */
export function ReadReport({ report, caption, onRetry, busy = false }: {
  report: ReadReportView
  /** Which file this is, when several are shown together. */
  caption?: string
  onRetry?: () => void
  busy?: boolean
}) {
  const [open, setOpen] = useState(false)
  const id = `rr-log-${report.notes[0]?.key ?? 'none'}-${caption ?? ''}`.replace(/\W+/g, '-')
  if (report.notes.length === 0) return null
  return (
    <section className="rr" aria-label="Reading log">
      <div className="rr-bar">
        <span className="rr-bar-title">{caption ? `Reading log · ${caption}` : 'Reading log'}</span>
        <LogToggle open={open} count={report.notes.length} onToggle={() => setOpen((v) => !v)} controls={id} />
      </div>
      {open && <div id={id} className="rr-panel"><ReadNotes notes={report.notes} onRetry={onRetry} busy={busy} /></div>}
    </section>
  )
}
