import type { ReportResult } from './report-api'

export type ReportKey = 'funnel' | 'staff' | 'source' | 'orders' | 'rejections' | 'advance'

export const REPORTS: { key: ReportKey; label: string; title: string; note: string }[] = [
  { key: 'funnel', label: 'Pipeline', title: 'Pipeline funnel', note: 'How many candidates reached each stage, and how many were lost before the next one. Only candidates matched to an order in this period are counted. A candidate who is only waiting still counts as active.' },
  { key: 'staff', label: 'Staff performance', title: 'Staff performance', note: 'Candidates given to each person, tasks finished, average hours per stage, and overdue tasks sent to the admin.' },
  { key: 'source', label: 'Sources', title: 'Candidate sources', note: 'Where new candidates came from in this period: each email account, WhatsApp, and candidates added by hand.' },
  { key: 'orders', label: 'Order status', title: 'Order status', note: 'For each order: open positions compared with candidates matched, selected and deployed.' },
  { key: 'rejections', label: 'Rejections', title: 'Rejections by stage and reason', note: 'Candidates whose application ended in this period, such as failed prescreen, rejected at interview or medically unfit.' },
  { key: 'advance', label: 'Advance collections', title: 'Advance collections', note: 'Advance money collected from each candidate, and per month. Each currency is added up separately.' },
]

/**
 * Categorical order: the palette's primary first, then fixed hues. The values are CSS variables
 * defined on `.rv` (styles/reports-v2.css) so they follow the palette and light/dark; a series keeps
 * its colour in every chart.
 */
export const SERIES = { primary: 'var(--rv-c1)', teal: 'var(--rv-c2)', amber: 'var(--rv-c3)', violet: 'var(--rv-c4)', rose: 'var(--rv-c5)', green: 'var(--rv-c6)', soft: 'var(--rv-c-soft)', bad: 'var(--rv-c-bad)' }

export const REASON_COLOURS: Record<string, string> = {
  prescreen_failed: SERIES.primary, not_interested: SERIES.amber, resume_not_received: SERIES.teal,
  interview_rejected: SERIES.violet, medical_unfit: SERIES.rose, unmapped: 'var(--rv-c-neutral)',
}

export const SOURCE_COLOURS: Record<string, string> = { email: SERIES.primary, whatsapp: SERIES.teal, walk_in: SERIES.amber, referral: SERIES.violet, other: SERIES.rose }

export const MANUAL_SOURCES = ['walk_in', 'referral', 'other']

export type Pipeline = ReportResult['pipeline'][number]

/** Drop-off between consecutive stages: candidates that reached stage n but never reached n + 1. */
export function stageDropOff(pipeline: Pipeline[], index: number) {
  const next = pipeline[index + 1]
  if (!next) return null
  const lost = Math.max(0, pipeline[index].reached - next.reached)
  const percent = pipeline[index].reached ? Math.round((lost / pipeline[index].reached) * 1000) / 10 : 0
  return { lost, percent }
}

export const fmtHours = (hours: number | null | undefined) => hours == null ? '—' : `${hours.toLocaleString(undefined, { maximumFractionDigits: 1 })} h`
export const fmtMoney = (amount: string | number) => Number(amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
export const fmtMonth = (month: string) => {
  const [year, number] = month.split('-').map(Number)
  return new Date(Date.UTC(year, (number || 1) - 1, 1)).toLocaleDateString(undefined, { month: 'short', year: 'numeric', timeZone: 'UTC' })
}
export const fmtDate = (iso: string) => new Date(`${iso}T12:00:00Z`).toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' })
