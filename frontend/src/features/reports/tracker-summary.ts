import type { TrackerRow } from './client-tracker-model'

/** The stages a recruiter waits on, as the Dashboard card names them (stage numbers are those of the Client tracker, 1 to 13). */
export const WAITING_STAGES: readonly { no: number; label: string }[] = [
  { no: 5, label: 'Offer' }, { no: 6, label: 'Advance' }, { no: 7, label: 'Passport' }, { no: 8, label: 'Medical' }, { no: 9, label: 'PCC' },
  { no: 10, label: 'Visa' }, { no: 11, label: 'Review' }, { no: 12, label: 'Stamp' }, { no: 13, label: 'Air ticket' },
]

export interface TrackerSummary { total: number; inProgress: number; travelled: number; waiting: { no: number; label: string; count: number }[] }

/** The numbers of the Dashboard card: placements still in progress, placements that travelled, and how many wait at each stage (travelled ones wait nowhere). */
export function summarizeTracker(rows: readonly Pick<TrackerRow, 'stageNo' | 'travelled'>[]): TrackerSummary {
  const travelled = rows.filter(row => row.travelled).length
  const waitingAt = new Map<number, number>()
  for (const row of rows) if (!row.travelled) waitingAt.set(row.stageNo, (waitingAt.get(row.stageNo) ?? 0) + 1)
  return {
    total: rows.length,
    inProgress: rows.length - travelled,
    travelled,
    waiting: WAITING_STAGES.map(stage => ({ ...stage, count: waitingAt.get(stage.no) ?? 0 })),
  }
}

/** Whether the Client tracker tab reads `?stage=` from the address. It does not yet, so the chips open the whole tracker; set to true once it does. */
export const TRACKER_READS_STAGE_PARAM = true

/** Where a Dashboard link goes: the tracker tab, narrowed to one stage when the tracker supports it. */
export function trackerLink(stage?: number, readsStage: boolean = TRACKER_READS_STAGE_PARAM): string {
  return stage !== undefined && readsStage ? `/reports?tab=tracker&stage=${stage}` : '/reports?tab=tracker'
}
