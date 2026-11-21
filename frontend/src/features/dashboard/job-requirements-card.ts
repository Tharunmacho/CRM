/** The dashboard's "Job requirements" card, as a pure function so it can be tested. */

/** What GET /job-requirements?status=active&limit=1 carries for the card: `total` counts the active rows; `summary` the deadline numbers. */
export interface JobRequirementsCardData {
  total?: number | null
  summary?: { active?: number | null; dueSoon?: number | null; overdue?: number | null } | null
}

export interface JobRequirementsCard { value: number | null; note: string; danger: boolean }

const count = (value: unknown): number => (typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : 0)

/**
 * Big number: active requirements (the summary's figure, else the read's total). Caption: "N due soon" (deadline within 7 days), with
 * ", M overdue" added and the caption in red when any deadline has passed; "none due soon" when nothing is near.
 */
export function jobRequirementsCard(data: JobRequirementsCardData | null | undefined): JobRequirementsCard {
  if (!data) return { value: null, note: 'loading', danger: false }
  const active = typeof data.summary?.active === 'number' ? count(data.summary.active) : count(data.total)
  const due = count(data.summary?.dueSoon)
  const overdue = count(data.summary?.overdue)
  const soon = due > 0 ? `${due} due soon` : 'none due soon'
  return { value: active, note: overdue > 0 ? `${soon}, ${overdue} overdue` : soon, danger: overdue > 0 }
}
