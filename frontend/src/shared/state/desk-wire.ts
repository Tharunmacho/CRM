/**
 * The compact workspace snapshot as it travels, and the same snapshot as the screens read it.
 *
 * `GET /api/bootstrap?compact=true&slim=true` (and `/api/auth/session?workspace=true&compact=true&slim=true`) leaves out what would
 * otherwise grow with every candidate and is not needed to list one:
 *   - keys that hold their default (null, [], 0, false ...) are not written; `rowDefaults` names them once;
 *   - a candidate's document slots, with every file's name, id, uploader and timestamps, are replaced by
 *     `documentCounts` ({ passport: 2 }: files held per slot); `documentLabels` gives the slots' names.
 * `expandDesk` puts all of that back, so a row looks exactly as it did when the server sent it whole, except that a
 * candidate's slots list only the slots that hold a file and carry no `versions`. `documentsSummary` marks such a
 * candidate; a screen that shows the files themselves reads that one candidate (`useCandidateDocuments`).
 *
 * A snapshot without `rowDefaults` (an older server, a remembered copy from before this) is returned as it is.
 */
import type { Candidate, DocumentSlot } from '@/shared/model/types'

type Row = Record<string, unknown>

/** Collections whose rows were trimmed, as the server names them in `rowDefaults`. */
const TRIMMED = ['candidates', 'applications', 'tasks', 'orders', 'companies', 'clients'] as const

/** A default is shared in the table, so a row gets its own copy of a list or object (nobody can change another row's). */
function own(value: unknown): unknown {
  if (Array.isArray(value)) return [...value]
  if (value !== null && typeof value === 'object') return { ...(value as object) }
  return value
}

function fill(row: Row, defaults: Row): Row {
  const out: Row = { ...row }
  for (const key in defaults) if (!(key in out)) out[key] = own(defaults[key])
  return out
}

function slotsOf(counts: Record<string, number> | undefined, labels: Record<string, string>): DocumentSlot[] {
  return Object.keys(counts ?? {}).map((key) => ({ key, label: labels[key] ?? key, versions: [], fileUploaded: true }))
}

export interface DeskWire {
  rowDefaults?: Record<string, Row>
  documentLabels?: Record<string, string>
  /** Digest of the rest of the body; also the response's ETag. Sent back as If-None-Match to ask "changed?". */
  revision?: string
}

/** The snapshot the screens use, and the revision it came with (if the server sent one). */
export function expandDesk<T extends object>(wire: T & DeskWire): { desk: T; revision: string | undefined } {
  const { rowDefaults, documentLabels, revision, ...rest } = wire
  if (!rowDefaults) return { desk: wire, revision }
  const desk = rest as unknown as Record<string, unknown>
  for (const name of TRIMMED) {
    const rows = desk[name]
    const defaults = rowDefaults[name]
    if (!Array.isArray(rows) || !defaults) continue
    desk[name] = rows.map((row: Row) => fill(row, defaults))
  }
  const stageDefaults = rowDefaults.stages
  if (stageDefaults && Array.isArray(desk.applications)) {
    desk.applications = (desk.applications as Row[]).map((application) =>
      Array.isArray(application.stages) && application.stages.length > 0
        ? { ...application, stages: (application.stages as Row[]).map((stage) => fill(stage, stageDefaults)) }
        : application)
  }
  if (Array.isArray(desk.candidates)) {
    const labels = documentLabels ?? {}
    desk.candidates = (desk.candidates as Row[]).map((candidate) => ({
      ...candidate,
      documents: slotsOf(candidate.documentCounts as Record<string, number> | undefined, labels),
      documentsSummary: true,
    }))
  }
  return { desk: desk as T, revision }
}

/** True when this candidate came from the compact snapshot, so its `documents` lack the files. */
export function hasOnlyDocumentSummary(candidate: Pick<Candidate, 'documentsSummary'> | null | undefined): boolean {
  return candidate?.documentsSummary === true
}
