import type { Application, Candidate } from '../../shared/model/types'

export type ActivityTone = 'ok' | 'warn' | 'bad' | 'info' | 'plain'

export interface ActivityEvent {
  id: string
  at: string
  title: string
  detail?: string
  by?: string
  tone: ActivityTone
}

export interface ActivityLookups {
  stageTitle: (stage: number) => string
  closureLabel: (reason: NonNullable<Application['closureReason']>) => string
  orderLabel: (orderId: string) => string
  userName: (userId: string | null | undefined) => string
  sourceLabel: (source: string) => string
}

const NEGATIVE = ['Failed', 'Rejected', 'Unfit', 'Not forwarded']

/**
 * Everything the saved record can say about a candidate, newest first: when they were added, the
 * initial call, and for each application when it was matched, every recorded stage outcome (with
 * who and when), reopenings, and how it ended. Built only from data already on the page.
 */
export function buildActivity(cand: Candidate, apps: Application[], look: ActivityLookups): ActivityEvent[] {
  const events: ActivityEvent[] = [
    { id: `added-${cand.id}`, at: cand.addedAt, title: 'Added to the Data Bank', detail: look.sourceLabel(cand.source) + (cand.sourceDetail ? ` · ${cand.sourceDetail}` : ''), tone: 'plain' },
  ]
  if (cand.callOutcome) {
    const { called, interested, remarks, recordedAt } = cand.callOutcome
    events.push({
      id: `call-${cand.id}`,
      at: recordedAt,
      title: !called ? 'Initial call not made yet' : interested === 'yes' ? 'Initial call: interested' : interested === 'no' ? 'Initial call: not interested' : 'Initial call: not reachable',
      detail: remarks || undefined,
      by: look.userName(cand.ownerId),
      tone: !called || interested === 'unreachable' ? 'warn' : interested === 'yes' ? 'ok' : 'bad',
    })
  }
  for (const app of apps) {
    const order = look.orderLabel(app.orderId)
    events.push({ id: `map-${app.id}`, at: app.createdAt, title: `Matched to ${order}`, tone: 'info' })
    for (const record of app.stages) {
      const negative = NEGATIVE.includes(record.outcome)
      const attempt = app.stages.filter((item) => item.stage === record.stage).length > 1 ? ` (attempt ${record.attempt})` : ''
      events.push({
        id: `stage-${app.id}-${record.stage}-${record.attempt}-${record.completedAt}`,
        at: record.completedAt,
        title: `Stage ${record.stage}, ${look.stageTitle(record.stage)}${attempt}: ${record.outcome}`,
        detail: record.reopenedReason ? `Reopened later: ${record.reopenedReason}` : order,
        by: look.userName(record.completedBy),
        tone: record.reopenedReason ? 'warn' : negative ? 'bad' : 'ok',
      })
    }
    if (app.status === 'deployed') events.push({ id: `end-${app.id}`, at: app.updatedAt, title: `Deployed through ${order}`, tone: 'ok' })
    else if (app.status === 'closed') events.push({ id: `end-${app.id}`, at: app.updatedAt, title: `${order} closed${app.closureReason ? `: ${look.closureLabel(app.closureReason)}` : ''}`, tone: 'bad' })
  }
  return events.sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime())
}
