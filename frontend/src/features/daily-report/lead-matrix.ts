import type { DailyReport, StaffRow } from './daily-report-types'
import { reportPeople } from './daily-report-view'

/*
 * The Lead sheet report's matrix: one row per recruiter, one column per lead status in the Lead sheet's own order (the nine statuses, then the status
 * templates that hold leads: Ringing, No incoming calls, Not eligible ...), plus the row of leads nobody owns and a Total that ties to leadSheet.
 * A figure the server did not send (an older server has no `byStatusPerStaff`) is null: the table shows a dash, never an error or a made-up zero.
 */

export const UNASSIGNED_ID = 'unassigned'

export interface MatrixColumn { key: string; label: string; template: boolean }
export interface MatrixRow {
  id: string
  name: string
  /** The leads nobody owns: a plain row, not a person to pick. */
  unassigned: boolean
  idle: boolean
  /** Leads the person holds now (null for the Unassigned row). */
  assigned: number | null
  counts: Record<string, number | null>
  /** Leads called or updated in the period (null for the Unassigned row). */
  contacted: number | null
  pct: number | null
}
export interface LeadMatrix {
  columns: MatrixColumn[]
  rows: MatrixRow[]
  total: MatrixRow | null
  /** The server sent the per-recruiter status counts. */
  hasCounts: boolean
}
export interface StatusNow { key: string; label: string; count: number; pct: number }

type Source = Pick<DailyReport, 'leadSheet' | 'staff' | 'scope'>

export function contactedShare(assigned: number | null, contacted: number | null): number | null {
  if (!assigned || assigned <= 0 || contacted == null) return null
  return Math.min(100, Math.round((contacted / assigned) * 100))
}

export function matrixColumns(sheet: DailyReport['leadSheet']): MatrixColumn[] {
  if (!sheet) return []
  if (sheet.statusColumns?.length) return sheet.statusColumns.map(c => ({ key: c.key, label: c.label, template: !!c.template }))
  return (sheet.byStatus ?? []).map(c => ({ key: c.key, label: c.label, template: false }))
}

const sumOrNull = (values: (number | null)[]) => (values.some(v => v == null) ? null : values.reduce<number>((total, v) => total + (v ?? 0), 0))

/** `staff`: the recruiter the page is narrowed to ('' for everyone); `roster` supplies a picked person the report has no row for. */
export function leadMatrix(report: Source, staff: string, roster: StaffRow[] = []): LeadMatrix {
  const sheet = report.leadSheet
  const columns = matrixColumns(sheet)
  const per = new Map((sheet?.byStatusPerStaff ?? []).map(entry => [entry.id, entry.counts]))
  const hasCounts = per.size > 0
  const countsOf = (id: string): Record<string, number | null> => {
    const found = per.get(id)
    return Object.fromEntries(columns.map(c => [c.key, found ? found[c.key] ?? 0 : null]))
  }
  const team = report.scope === 'team'
  // Recruiters and the MD are rows; the Administrator account is not (the owner: "admin alone no need to show").
  let people = reportPeople(report.staff)
  if (staff) {
    people = people.filter(row => row.id === staff)
    if (!people.length) people = reportPeople(roster).filter(row => row.id === staff)
  }
  const rows: MatrixRow[] = [...people].sort((a, b) => a.name.localeCompare(b.name)).map(row => {
    const assigned = row.leads?.assigned ?? null
    const contacted = row.leads?.contacted ?? row.metrics?.leadsContacted ?? null
    return { id: row.id, name: row.name, unassigned: false, idle: !!row.idle, assigned, counts: countsOf(row.id), contacted, pct: contactedShare(assigned, contacted) }
  })
  const whole = team && !staff
  // Leads held by the Administrator account have no recruiter either: they join the Unassigned row, so the Total still ties to the Lead sheet screen.
  const shown = new Set(reportPeople(report.staff).map(row => row.id))
  const unowned = [UNASSIGNED_ID, ...report.staff.filter(row => !shown.has(row.id)).map(row => row.id)].filter(id => per.has(id))
  if (whole && hasCounts && unowned.length) {
    const counts = Object.fromEntries(columns.map(c => [c.key, unowned.reduce((all, id) => all + (per.get(id)?.[c.key] ?? 0), 0)]))
    rows.push({ id: UNASSIGNED_ID, name: 'Unassigned', unassigned: true, idle: false, assigned: null, counts, contacted: null, pct: null })
  }
  if (!rows.length) return { columns, rows, total: null, hasCounts }
  const assigned = sumOrNull(rows.filter(r => !r.unassigned).map(r => r.assigned))
  const contacted = sumOrNull(rows.filter(r => !r.unassigned).map(r => r.contacted))
  const total: MatrixRow = {
    id: 'total', name: 'Total', unassigned: false, idle: false, assigned, contacted, pct: contactedShare(assigned, contacted),
    counts: Object.fromEntries(columns.map(c => [c.key, sumOrNull(rows.map(r => r.counts[c.key]))])),
  }
  return { columns, rows, total, hasCounts }
}

/** The status table: every status with its leads and its share of the selection, in the matrix's order. Falls back to the sheet's own byStatus when the whole sheet is shown by an older server. */
export function statusNow(matrix: LeadMatrix, report: Source, staff: string): StatusNow[] {
  const sheet = report.leadSheet
  if (!sheet) return []
  let counts: Record<string, number | null> | null = matrix.hasCounts ? matrix.total?.counts ?? null : null
  if (!counts && !staff) counts = Object.fromEntries(sheet.byStatus.map(item => [item.key, item.count]))
  if (!counts) return []
  const all = matrix.columns.map(c => ({ key: c.key, label: c.label, count: counts![c.key] ?? 0 }))
  const sum = all.reduce((total, item) => total + item.count, 0)
  return all.map(item => ({ ...item, pct: sum > 0 ? Math.round((item.count / sum) * 1000) / 10 : 0 }))
}

/** A column heading on at most two lines, broken between words only (never inside one), as evenly as the words allow: "Resume requested" -> ["Resume", "requested"]. */
export function headLines(label: string): string[] {
  const words = label.trim().split(/\s+/).filter(Boolean)
  if (words.length <= 1) return words
  let best = 1
  let least = Infinity
  for (let cut = 1; cut < words.length; cut += 1) {
    const widest = Math.max(words.slice(0, cut).join(' ').length, words.slice(cut).join(' ').length)
    if (widest < least) { least = widest; best = cut }
  }
  return [words.slice(0, best).join(' '), words.slice(best).join(' ')]
}
