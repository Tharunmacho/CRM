import { Check, ChevronRight, Route } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { Select } from '@/shared/ui/primitives'
import { formatFormDate, STAGE_NAMES } from '../form-model'
import { PROCESS_EMPTY_LINE, stageRows, stagesDoneText } from '../process-stages'
import type { ApplicationFormData } from '../types'

type StageForm = Pick<ApplicationFormData, 'tracking' | 'application' | 'documents' | 'deployment'>
type StageProps = { form: StageForm; canOpen: boolean; onOpen: (stage: number) => void }

/** The sixth assignment field, directly beside Recruiter. Opening it never moves a stage. */
export function StageControl({ form, canOpen, onOpen }: StageProps) {
  const application = form.application
  const current = form.tracking.summary.currentStage || application?.currentStage || 1
  const deployed = application?.status === 'deployed'
  const closed = application && !['active', '', 'deployed'].includes(application.status)
  const label = !application ? 'Choose a job first' : deployed ? 'Deployed' : closed ? 'Application closed' : STAGE_NAMES[current] || `Stage ${current}`
  return (
    <div className="af-assign-field af-stage-field" data-assign="stage">
      <div className="af-assign-label"><label htmlFor="af-assign-stage">Current stage</label><span className="af-stage-manual">Manual</span></div>
      <button id="af-assign-stage" type="button" className={cn('af-stage-control', deployed && 'is-deployed')}
        disabled={!application || !canOpen} onClick={() => onOpen(current)} aria-label={`${label}. Manage recruitment stages`}>
        <span className="af-stage-control-icon" aria-hidden="true">{deployed ? <Check /> : <Route />}</span>
        <span className="af-stage-control-name">{label}</span>
        {application && <small>{current}/11</small>}<ChevronRight aria-hidden="true" />
      </button>
    </div>
  )
}

/** Selecting any stage replaces its feedback card in place. */
export function RecruitmentStages({ form, canOpen, onOpen, selectedStage }: StageProps & { selectedStage: number | null }) {
  const rows = stageRows(form, canOpen)
  const current = selectedStage || form.tracking.summary.currentStage || form.application?.currentStage || 1
  return (
    <section className="af-recruitment-stages" id="af-process" aria-labelledby="af-process-title">
      <div className="af-recruitment-heading">
        <div><Route aria-hidden="true" /><h3 id="af-process-title">Recruitment stages</h3></div>
        {form.application && <span className="af-stage-total">{stagesDoneText(rows)}</span>}
      </div>
      <p className="af-stage-instruction">Choose a stage to view its feedback.</p>
      <div className="af-stage-mobile-picker">
        <label htmlFor="af-stage-picker">Stage and feedback</label>
        <Select id="af-stage-picker" aria-label="Choose recruitment stage" value={String(current)} disabled={!form.application || !canOpen} onChange={(event) => onOpen(Number(event.target.value))}>
          {rows.map((row) => <option key={row.stage} value={row.stage} disabled={!row.openable}>{row.stage}. {row.name}{row.key === 'current' ? ' · In progress' : row.key === 'done' ? ' · Completed' : row.key === 'failed' ? ' · Closed' : ''}</option>)}
        </Select>
      </div>
      <ol className="af-stage-grid">
        {rows.map((row) => (
          <li key={row.stage} data-stage={row.stage}>
            <button type="button" className={cn('af-stage-tile', `is-${row.key}`, selectedStage === row.stage && 'is-selected')} disabled={!row.openable}
              onClick={() => onOpen(row.stage)} aria-current={row.key === 'current' ? 'step' : undefined}
              aria-pressed={selectedStage === row.stage} aria-controls="af-stage-feedback"
              aria-label={`Open stage ${row.stage}: ${row.name}, ${row.key === 'locked' ? 'upcoming' : row.chip.toLowerCase()}`}>
              <span className="af-stage-tile-number" aria-hidden="true">{row.key === 'done' ? <Check /> : row.stage}</span>
              <span className="af-stage-tile-copy"><b>{row.name}</b>{row.key !== 'locked' && <small>
                {row.key === 'done' ? row.date ? formatFormDate(row.date) : 'Completed' : row.key === 'current' ? 'In progress' : 'Closed'}
              </small>}</span>
            </button>
          </li>
        ))}
      </ol>
      <p className="af-stage-footnote">{form.application ? 'Documents are tracked separately and do not block stage changes.' : PROCESS_EMPTY_LINE}</p>
    </section>
  )
}
