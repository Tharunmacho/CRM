import { memo, useEffect, useRef, useState } from 'react'
import { Check, CircleAlert, Clock, LoaderCircle, Minus, TriangleAlert } from 'lucide-react'
import { isStalled, resultStages, type BatchReading, type ResultStage } from '../import-view'
import type { ImportedStatus } from '../import-api'

const MARK: Record<ResultStage['state'], JSX.Element> = {
  done: <Check strokeWidth={3} />,
  active: <LoaderCircle className="animate-spin" />,
  todo: <Minus />,
  warn: <TriangleAlert />,
  error: <CircleAlert />,
}

/** Filed → Read → Details filled → Done, from what the status endpoint says about this candidate. */
export const ResultTrack = memo(function ResultTrack({ status }: { status?: ImportedStatus }) {
  const stages = resultStages(status)
  return (
    <ol className="ic-track" aria-label="Progress of this candidate">
      {stages.map((stage) => (
        <li key={stage.key} className={`ic-stage is-${stage.state}`}>
          <span className="ic-stage-dot" aria-hidden="true">{MARK[stage.state]}</span>
          <span className="ic-stage-text">
            <b>{stage.label}</b>
            {stage.note && <small>{stage.note}</small>}
          </span>
        </li>
      ))}
    </ol>
  )
})

/** The whole batch while the documents are read: one bar, the numbers, and an honest note when nothing has moved for a while. */
export const BatchProgress = memo(function BatchProgress({ batch, imported, failed }: { batch: BatchReading; imported: number; failed: number }) {
  const last = useRef({ finished: batch.finished, at: Date.now() })
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (batch.finished !== last.current.finished) last.current = { finished: batch.finished, at: Date.now() }
  }, [batch.finished])
  const reading = batch.open > 0
  useEffect(() => {
    if (!reading) return
    const timer = window.setInterval(() => setNow(Date.now()), 5000)
    return () => window.clearInterval(timer)
  }, [reading])
  if (batch.total === 0) {
    return (
      <section className="ic-batch is-next" aria-live="polite">
        <span className="ic-batch-icon" aria-hidden="true"><Check strokeWidth={3} /></span>
        <div>
          <h2>{imported === 1 ? 'The candidate is imported' : `${imported} candidates are imported`}{failed > 0 ? `, ${failed} failed` : ''}</h2>
        </div>
      </section>
    )
  }
  const stalled = reading && isStalled(last.current.at, now)
  const percent = Math.round(batch.fraction * 100)
  return (
    <section className={`ic-batch${batch.complete ? ' is-done' : ''}`} aria-live="polite">
      <div className="ic-batch-top">
        <span className="ic-batch-icon" aria-hidden="true">{batch.complete ? <Check strokeWidth={3} /> : stalled ? <Clock /> : <LoaderCircle className="animate-spin" />}</span>
        <div>
          <h2>{batch.complete ? 'All documents are read' : 'Reading the documents'}</h2>
          <p>{batch.finished} of {batch.total} documents done{batch.failed > 0 ? `, ${batch.failed} could not be read` : ''}{batch.review > 0 ? `, ${batch.review} to check on the candidate's page` : ''}.</p>
        </div>
        <span className="ic-batch-percent" aria-hidden="true">{percent}%</span>
      </div>
      <div className="ic-bar" role="progressbar" aria-label="Documents read" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><span style={{ width: `${Math.max(batch.finished > 0 ? 3 : 0, percent)}%` }} /></div>
      {stalled && (
        <p className="ic-batch-wait">
          Still waiting for the document reader. The documents stay queued and fill in as soon as the reader runs, so you can leave this page. If this never moves, the reader may not be switched on: ask your administrator.
        </p>
      )}
    </section>
  )
})

