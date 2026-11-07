import { AlertTriangle, Check, ChevronRight, Lock, Route, Unlock, type LucideIcon } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { formatFormDate } from '../form-model'
import { PROCESS_EMPTY_LINE, stageRows, stagesDoneText, type StageIcon, type StageRow } from '../process-stages'
import type { ApplicationFormData } from '../types'
import { SectionHeader } from './section-header'

const ICONS: Record<StageIcon, LucideIcon> = { lock: Lock, unlock: Unlock, check: Check, alert: AlertTriangle }

function StageContent({ row }: { row: StageRow }) {
  const Icon = ICONS[row.icon]
  return (
    <>
      <span className={cn('af-stage-icon', `is-${row.key}`)} aria-hidden="true"><Icon /></span>
      <span className="af-stage-main">
        <b><span className="af-stage-no tnum">{row.stage}</span>{row.name}</b>
        {row.documents.length > 0 && (
          <ul className="af-stage-docs" aria-label={`Documents for ${row.name}`}>
            {row.documents.map((item) => (
              <li key={item.slot} className={item.uploaded ? 'is-done' : 'is-pending'}>
                {item.uploaded ? <Check aria-label="Uploaded" /> : <i aria-label="Pending" />}{item.label}
              </li>
            ))}
          </ul>
        )}
        {row.readiness && row.readiness.tone === 'ready' && <span className={cn('af-stage-ready', `is-${row.readiness.tone}`)}>{row.readiness.text}</span>}
      </span>
      <span className={cn('af-stage-chip', `is-${row.key}`)}>{row.chip}</span>
      <span className="af-stage-date">{row.date ? formatFormDate(row.date) : ''}</span>
      <ChevronRight className="af-stage-go" aria-hidden="true" />
    </>
  )
}

/**
 * Recruitment process tracking, a section of the working card: all eleven stages as rows on fixed columns (icon, number and name with its
 * documents, status, date). A stage the candidate has not reached shows a lock, the current one an open lock, a finished one a
 * tick and its date. The lock opens by itself when the live feed brings the candidate to the stage.
 */
export function ProcessSection({
  form,
  canOpen,
  onOpen,
}: {
  form: Pick<ApplicationFormData, 'tracking' | 'application' | 'documents' | 'deployment'>
  canOpen: boolean
  onOpen: (stage: number) => void
}) {
  const rows = stageRows(form, canOpen)
  return (
    <section className="af-sec af-process" id="af-process" aria-labelledby="af-process-title">
      <SectionHeader icon={<Route />} id="af-process-title" title="Recruitment process tracking" chip={form.application ? <span className="af-sh-count">{stagesDoneText(rows)}</span> : undefined} />
      <ol className="af-stages">
        {rows.map((row) => (
          <li key={row.stage} className={cn('af-stage', `is-${row.key}`)} data-stage={row.stage}>
            {row.openable ? (
              <button type="button" className="af-stage-row is-openable" onClick={() => onOpen(row.stage)} aria-label={`Open stage ${row.stage}: ${row.name}`}><StageContent row={row} /></button>
            ) : (
              <div className="af-stage-row"><StageContent row={row} /></div>
            )}
          </li>
        ))}
      </ol>
      {!form.application && <p className="af-process-empty">{PROCESS_EMPTY_LINE}</p>}
    </section>
  )
}
