/**
 * The "Process tracking" section: all eleven stages, each locked until the candidate reaches it, open while the candidate is on it
 * and ticked once it is done, with the documents that belong to the stage and, on the last stage, whether the candidate is ready
 * to deploy. The documents themselves are never locked (see `document-rows.ts`); the stages are. Plain data in, plain data out,
 * so the rules are tested without a browser.
 */
import { DOCUMENT_SLOTS, documentLabel } from '@/shared/model/document-slots'
import { STAGE_NAMES } from './form-model'
import type { ApplicationFormData } from './types'

export type StageKey = 'locked' | 'current' | 'done' | 'failed'
export type StageIcon = 'lock' | 'unlock' | 'check' | 'alert'
export type StageChip = 'Locked' | 'In progress' | 'Completed' | 'Failed'

export const STAGE_COUNT = 11
export const PROCESS_EMPTY_LINE = 'Choose a job at the top to start the process.'

const CHIPS: Record<StageKey, { icon: StageIcon; chip: StageChip }> = {
  locked: { icon: 'lock', chip: 'Locked' },
  current: { icon: 'unlock', chip: 'In progress' },
  done: { icon: 'check', chip: 'Completed' },
  failed: { icon: 'alert', chip: 'Failed' },
}

export interface StageDocument { slot: string; label: string; uploaded: boolean }
export interface Readiness { text: string; tone: 'ready' | 'needs' }

export interface StageRow {
  stage: number
  name: string
  key: StageKey
  icon: StageIcon
  chip: StageChip
  /** The completion date as the server sent it; empty until the stage is done. */
  date: string
  /** Who completed it. */
  completedBy: string
  /** The documents that belong to this stage, with whether each has arrived. */
  documents: StageDocument[]
  /** Only the last stage: "Ready to deploy" or "Needs: Medical, Visa". Absent when the server says nothing. */
  readiness: Readiness | null
  /** Clicking the row opens the stage's own form (people who can work the candidate, on a candidate that has a job). */
  openable: boolean
}

/** "Ready to deploy", or "Needs: Medical, Visa"; null when the server does not send the deployment block (an older server). */
export function deploymentText(deployment: ApplicationFormData['deployment'] | null | undefined): Readiness | null {
  if (!deployment) return null
  if (deployment.ready) return { text: 'Ready to deploy', tone: 'ready' }
  const names = deployment.missing.map((item) => documentLabel(item.slot))
  return { text: names.length ? `Needs: ${names.join(', ')}` : 'Not ready to deploy', tone: 'needs' }
}

/** The documents that belong to a stage: the shared list says from which stage each one is expected. */
export function stageDocuments(stage: number, documents: ApplicationFormData['documents']): StageDocument[] {
  return DOCUMENT_SLOTS
    .filter((entry) => entry.fromStage === stage)
    .map((entry) => ({ slot: entry.slot, label: entry.label, uploaded: documents[entry.slot]?.uploaded === true }))
}

/**
 * The eleven rows. With no job every stage is locked. With a job, a stage is done when the server says it is completed (or the
 * candidate is already past it), current when it is the stage the application is on, and locked otherwise; it changes by itself as
 * the live feed brings the next stage in.
 */
export function stageRows(
  form: Pick<ApplicationFormData, 'tracking' | 'application' | 'documents' | 'deployment'>,
  canOpen: boolean,
): StageRow[] {
  const application = form.application
  const active = Boolean(application) && (application?.status === 'active' || application?.status === '')
  const currentStage = form.tracking.summary.currentStage || application?.currentStage || 0
  return Array.from({ length: STAGE_COUNT }, (_, index) => {
    const stage = index + 1
    const row = form.tracking.rows.find((item) => item.stage === stage && !item.extra)
    let key: StageKey = 'locked'
    if (application) {
      if (row?.status === 'completed') key = 'done'
      else if (row?.status === 'failed') key = 'failed'
      else if (stage < currentStage) key = 'done'
      else if (stage === currentStage && active) key = 'current'
    }
    return {
      stage,
      name: STAGE_NAMES[stage] ?? `Stage ${stage}`,
      key,
      ...CHIPS[key],
      date: key === 'done' ? row?.date ?? '' : '',
      completedBy: key === 'done' ? row?.completedBy ?? '' : '',
      documents: stageDocuments(stage, form.documents),
      readiness: stage === STAGE_COUNT ? deploymentText(form.deployment) : null,
      openable: canOpen && Boolean(application) && row?.tracked !== false,
    }
  })
}

/** "3 of 11 stages completed". */
export const stagesDoneText = (rows: Pick<StageRow, 'key'>[]): string => `${rows.filter((row) => row.key === 'done').length} of ${rows.length} stages completed`
