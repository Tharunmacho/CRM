import { CheckSquare, ChevronRight, Square, XSquare } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { TRACKING_STATUS_LABELS, formatFormDate, isOpenableRow, summariseTracking } from '../form-model'
import type { TrackingRow } from '../types'
import { Section } from './sections'

function StatusMark({ status }: { status: TrackingRow['status'] }) {
  if (status === 'completed') return <CheckSquare className="af-box is-done" aria-hidden="true" />
  if (status === 'not_applicable') return <Square className="af-box is-na" aria-hidden="true" />
  if (status === 'failed') return <XSquare className="af-box is-failed" aria-hidden="true" />
  return <Square className={cn('af-box', status === 'in_progress' && 'is-now')} aria-hidden="true" />
}

/** Section 7: one row per recruitment stage, live. Opening a row opens the stage's own form for people allowed to work it. */
export function TrackingSection({
  rows,
  hasOrder,
  canOpen,
  onOpen,
  closure,
}: {
  rows: TrackingRow[]
  hasOrder: boolean
  canOpen: boolean
  onOpen: (row: TrackingRow) => void
  closure?: string
}) {
  const summary = summariseTracking(rows)
  return (
    <Section no={7} aside={hasOrder && rows.length ? <span className="af-aside-note">{summary.completed} of {summary.total} stages completed{closure ? ` · ${closure}` : ''}</span> : undefined}>
      {!hasOrder || rows.length === 0 ? (
        <div className="af-rowtable">
          <div className="af-track-empty">
            {hasOrder ? 'No stage information yet.' : 'This candidate is not matched to an order, so there are no stages to track. Match them to an order to start the process.'}
          </div>
        </div>
      ) : (
        <>
          <div className="af-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={summary.percent} aria-label="Recruitment process progress">
            <span style={{ width: `${summary.percent}%` }} className={cn(summary.failed > 0 && 'has-failed')} />
          </div>
          <div className="af-track" role="table" aria-label="Recruitment process tracking">
            <div className="af-track-head" role="row">
              <span role="columnheader">Recruitment process</span>
              <span role="columnheader">Status</span>
              <span role="columnheader">Date</span>
              <span role="columnheader" className="af-track-by-h">Completed by</span>
            </div>
            {rows.map((row) => {
              const openable = canOpen && isOpenableRow(row)
              const content = (
                <>
                  <span className="af-track-name" role="cell">
                    <StatusMark status={row.status} />
                    <span className="af-track-label">
                      <b>{row.label}</b>
                      {row.extra && <small>Extra CRM stage</small>}
                      {!row.extra && row.systemLabel && row.systemLabel.toLowerCase() !== row.label.toLowerCase() && <small>{row.systemLabel}</small>}
                      {!row.tracked && <small>Not a stage in this system</small>}
                      {(row.outcome || row.note) && <small className="af-track-note">{[row.outcome, row.note].filter(Boolean).join(' · ')}</small>}
                    </span>
                  </span>
                  <span className="af-track-status" role="cell" data-label="Status"><span className={cn('af-status', `is-${row.status}`)}>{TRACKING_STATUS_LABELS[row.status]}</span></span>
                  <span className="af-track-date" role="cell" data-label="Date">{row.date ? formatFormDate(row.date) : <span className="af-empty">–</span>}</span>
                  <span className="af-track-by" role="cell" data-label="Completed by">{row.completedBy || <span className="af-empty">–</span>}</span>
                  {openable && <ChevronRight className="af-track-go" aria-hidden="true" />}
                </>
              )
              const classes = cn('af-track-row', `is-${row.status}`, row.current && 'is-current', openable && 'is-openable')
              return openable ? (
                <button type="button" role="row" className={classes} key={row.key} onClick={() => onOpen(row)} aria-label={`Open stage ${row.stage}: ${row.label}`}>{content}</button>
              ) : (
                <div role="row" className={classes} key={row.key}>{content}</div>
              )
            })}
          </div>
        </>
      )}
    </Section>
  )
}
