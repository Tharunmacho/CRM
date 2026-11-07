import { AlertTriangle, Check, ChevronDown, FileSearch, RotateCw } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { unmappedCountText, type ReadReportView } from '../read-report'
import '@/styles/add-candidate-v2.css'

/**
 * "What we read": the server's one-line summary, a chip per kind (green when the scan and the reader agree, amber when they differ),
 * notes for anything that needs a look, and the details that have no place on the form. Pure display: the host passes the built
 * report and, when it can read a file again, `onRetry` (the button shows only then).
 */
export function ReadReport({ report, caption, onRetry, busy = false }: {
  report: ReadReportView
  /** Which file this is, when several are shown together. */
  caption?: string
  onRetry?: () => void
  busy?: boolean
}) {
  const { headline, chips, notes, unmapped, unmappedCount } = report
  return (
    <section className="rr" aria-label="What we read">
      <header className="rr-head">
        <span className="rr-ico" aria-hidden><FileSearch size={16} /></span>
        <div className="rr-title">
          <b>What we read{caption ? <span className="rr-caption"> · {caption}</span> : null}</b>
          {headline && <p>{headline}</p>}
        </div>
      </header>

      {chips.length > 0 && (
        <ul className="rr-chips" aria-label="Documents read">
          {chips.map((chip) => (
            <li key={chip.kind} className={cn('rr-chip', `is-${chip.state}`)} title={chip.title || undefined}>
              {chip.state === 'verified' ? <Check size={14} strokeWidth={3} aria-hidden /> : chip.state === 'check' ? <AlertTriangle size={14} aria-hidden /> : <i aria-hidden />}
              <span>{chip.label}</span>
              {chip.state === 'verified' && <small>checked</small>}
            </li>
          ))}
        </ul>
      )}

      {notes.length > 0 && (
        <ul className="rr-notes">
          {notes.map((note) => (
            <li key={note.key} className={`is-${note.tone}`}>
              {note.tone === 'warn' && <AlertTriangle size={14} aria-hidden />}
              <span>{note.text}</span>
              {note.retry && onRetry && (
                <button type="button" className="rr-retry" disabled={busy} onClick={onRetry}><RotateCw size={13} aria-hidden />Read again</button>
              )}
            </li>
          ))}
        </ul>
      )}

      {unmapped.length > 0 && (
        <details className="rr-more">
          <summary>
            <ChevronDown size={14} aria-hidden />
            <span>Not placed on the form</span>
            <small className="tnum">{unmappedCount} {unmappedCount === 1 ? 'detail' : 'details'}</small>
          </summary>
          <ul className="rr-list">
            {unmapped.map((item) => (
              <li key={item.key}>
                <p><b>{item.kindName}</b> · {item.group} <span className="tnum rr-count">{unmappedCountText(item)}</span></p>
                {item.samples.length > 0 && (
                  <ul className="rr-samples">{item.samples.map((sample, index) => <li key={index}>{sample}</li>)}</ul>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}
