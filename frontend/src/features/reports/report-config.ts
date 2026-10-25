import type { ReportResult } from './report-api'

export type ReportKey = 'funnel' | 'staff' | 'source' | 'orders' | 'rejections' | 'advance'

/**
 * The six reports. `question` is the plain question the report answers (shown in the report list);
 * `note` says what it counts; `how` explains, line by line, how the numbers are worked out (these
 * restate the server's rules, they add none).
 */
export const REPORTS: { key: ReportKey; label: string; question: string; title: string; note: string; how: string[] }[] = [
  {
    key: 'funnel', label: 'Pipeline', question: 'Where do we lose people?', title: 'Pipeline funnel',
    note: 'How many candidates reached each stage, and how many were lost before the next one. Only candidates matched to an order in this period are counted. A candidate who is only waiting still counts as active.',
    how: [
      'Reached: candidates matched to an order in the period who got to this stage or went past it.',
      'Active or waiting: still sitting at this stage, not ended. Moved on: now at a later stage, or deployed. Ended here: the application was closed at this stage.',
      'Lost before next stage: candidates who reached this stage but never reached the next one (reached here minus reached next). It also includes people still waiting, so it can shrink as they move on.',
    ],
  },
  {
    key: 'staff', label: 'Staff performance', question: 'Who is carrying what?', title: 'Staff performance',
    note: 'Candidates given to each person, tasks finished, average hours per stage, and overdue tasks sent to the admin.',
    how: [
      'Assigned: candidates that arrived in the period and were first given to this person, within your filters.',
      'Average hours per stage: time from when a task is created to when its stage is finished. Stages with no finished task have no average; they are not counted as zero.',
      'Sent to admin: tasks that passed the 48-hour limit and were escalated. Missed updates: days an open task got no daily remark.',
    ],
  },
  {
    key: 'source', label: 'Sources', question: 'Where do candidates come from?', title: 'Candidate sources',
    note: 'Where new candidates came from in this period: each email account, WhatsApp, and candidates added by hand.',
    how: [
      'Counts candidates added in the period, with your filters, by the way they first reached the CRM.',
      'Email candidates are grouped by the connected mailbox that received the first message.',
      'Manual means walk-in, referral or other, entered by a person.',
    ],
  },
  {
    key: 'orders', label: 'Order status', question: 'Are orders getting filled?', title: 'Order status',
    note: 'For each order: open positions compared with candidates matched, selected and deployed.',
    how: [
      'Openings: how many people the order needs.',
      'Matched, selected and deployed count candidates who were added to the order during the chosen dates, so matched can be higher than openings.',
      'The client, order and job title filters choose which orders are listed.',
    ],
  },
  {
    key: 'rejections', label: 'Rejections', question: 'Why do applications end?', title: 'Rejections by stage and reason',
    note: 'Candidates whose application ended in this period, such as failed prescreen, rejected at interview or medically unfit.',
    how: [
      'Counts applications that ended inside the period, at the stage where they ended.',
      'Reasons are the closing reasons chosen when the stage is completed.',
      'Share is the count divided by all ended applications shown.',
    ],
  },
  {
    key: 'advance', label: 'Advance collections', question: 'How much money came in?', title: 'Advance collections',
    note: 'Advance money collected from each candidate, and per month. Each currency is added up separately.',
    how: [
      'An amount appears when stage 8 records the advance as collected.',
      'Currencies are never converted or mixed: each has its own total and chart.',
      'Months follow the collection date, in the agency time zone.',
    ],
  },
]

/**
 * Categorical order: logo blue, logo green, amber, violet, pink, cyan. The values are CSS variables
 * defined on `.rv` (styles/reports-v2.css) from the shared `--chart-1..6` tokens, so they follow light/dark;
 * a series keeps its colour in every chart. (`rose` is the fifth hue, whatever the skin makes it.)
 */
export const SERIES = { primary: 'var(--rv-c1)', teal: 'var(--rv-c2)', amber: 'var(--rv-c3)', violet: 'var(--rv-c4)', rose: 'var(--rv-c5)', green: 'var(--rv-c6)', soft: 'var(--rv-c-soft)', bad: 'var(--rv-c-bad)' }

export const REASON_COLOURS: Record<string, string> = {
  prescreen_failed: SERIES.primary, not_interested: SERIES.amber, resume_not_received: SERIES.teal,
  interview_rejected: SERIES.violet, medical_unfit: SERIES.rose, unmapped: 'var(--rv-c-neutral)',
}

export const SOURCE_COLOURS: Record<string, string> = { email: SERIES.primary, whatsapp: SERIES.teal, walk_in: SERIES.amber, referral: 'color-mix(in srgb, var(--rv-c1) 42%, white)', other: 'var(--rv-c-neutral)' }

export const MANUAL_SOURCES = ['walk_in', 'referral', 'other']

export type Pipeline = ReportResult['pipeline'][number]

/** Drop-off between consecutive stages: candidates that reached stage n but never reached n + 1.
 *  The server sends it (lostToNext); older answers fall back to the same subtraction. */
export function stageDropOff(pipeline: Pipeline[], index: number) {
  const next = pipeline[index + 1]
  if (!next) return null
  const row = pipeline[index]
  const lost = row.lostToNext ?? Math.max(0, row.reached - next.reached)
  const percent = row.lostToNextPercent ?? (row.reached ? Math.round((lost / row.reached) * 1000) / 10 : 0)
  return { lost, percent }
}

export const fmtHours = (hours: number | null | undefined) => hours == null ? '—' : `${hours.toLocaleString(undefined, { maximumFractionDigits: 1 })} h`
export const fmtMoney = (amount: string | number) => Number(amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
export const fmtMonth = (month: string) => {
  const [year, number] = month.split('-').map(Number)
  return new Date(Date.UTC(year, (number || 1) - 1, 1)).toLocaleDateString(undefined, { month: 'short', year: 'numeric', timeZone: 'UTC' })
}
export const pct = (part: number, total: number) => total ? Math.round(part / total * 1000) / 10 : 0
export const fmtDate = (iso: string) => new Date(`${iso}T12:00:00Z`).toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' })
