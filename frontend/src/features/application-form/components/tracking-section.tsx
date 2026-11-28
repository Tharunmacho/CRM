import { CheckSquare, ChevronRight, Square, XSquare } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { TRACKING_STATUS_LABELS, formatFormDate, isOpenableRow } from '../form-model'
import type { TrackingRow } from '../types'
import { Section } from './sections'

/** Done and not-started rows say it with their box alone; only the states that need attention get a word. */
const QUIET_STATUS: ReadonlySet<TrackingRow['status']> = new Set(['completed', 'not_started'])

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
  const stages = rows.filter(row => row.stage > 0 || (row.key === 'invoice_generated' && row.status === 'completed'))
  return (
    <Section no={7} aside={closure || undefined}>
      {!hasOrder || stages.length === 0 ? (
        <div className="af-rowtable">
          <div className="af-track-empty">
            {hasOrder ? 'No stage information yet.' : 'This candidate is not matched to a job yet, so there are no stages to track. Choose a job at the top of the page to start the process.'}
          </div>
        </div>
      ) : (
        <>
          <div className="af-track af-track-compact" role="table" aria-label="Recruitment process tracking">
            <div className="af-track-head" role="row">
              <span role="columnheader">Stage</span>
              <span role="columnheader">Result</span>
              <span role="columnheader">Date</span>
            </div>
            {stages.map((row) => {
              const openable = canOpen && isOpenableRow(row)
              const title = row.stage === 1 && row.status === 'completed' ? 'Prescreened' : row.systemLabel || row.label
              const outcome = row.outcome && !['completed manually', 'completed'].includes(row.outcome.toLowerCase()) ? row.outcome : ''
              const result = outcome || (QUIET_STATUS.has(row.status) ? '' : TRACKING_STATUS_LABELS[row.status])
              const content = (
                <>
                  <span className="af-track-name" role="cell">
                    <StatusMark status={row.status} />
                    <span className="af-track-label">
                      <b>{title}</b>
                    </span>
                  </span>
                  <span className="af-track-status" role="cell" data-label="Status">
                    {result && <span className={cn('af-status', `is-${row.status}`)}>{result}</span>}
                  </span>
                  <span className="af-track-date" role="cell">{row.date ? formatFormDate(row.date) : ''}</span>
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
