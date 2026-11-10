import { api, fileHref } from '@/shared/api/client'
import { MAX_DOCUMENT_BYTES } from '@/shared/lib/document-files'

/** One candidate's folder as picked in the browser. */
export interface PickedFolder {
  folder: string
  files: { path: string; file: File }[]
}

export interface PickedFolders {
  folders: PickedFolder[]
  /** A spreadsheet found loose in the picked folder (the filled-in sheet). */
  sheet: File | null
  ignored: number
}

export type ImportAction = 'skip' | 'documents_only' | 'in_progress' | 'deploy'

export interface ImportIssue {
  level: 'error' | 'warning' | 'info'
  message: string
}

export interface PlannedFile {
  path: string
  kind: string | null
  /** One scan can be several documents ("signed offer, passport, aadhaar"). */
  kinds: string[]
  labels: string[]
  label: string | null
  skipped: boolean
  problem: string | null
  guessed?: boolean
}

export interface StageCheck {
  stage: number
  title: string
  ok: boolean
  missing: string[]
}

export interface ImportPlan {
  folder: string
  name: string
  phone: string | null
  order: string | null
  recruiter: string | null
  files: PlannedFile[]
  issues: ImportIssue[]
  stages: StageCheck[]
  level: number
  completedThrough: number
  currentStage: number | null
  missingDocuments: string[]
  action: ImportAction
  duplicateOf: string | null
  row: Record<string, string>
  hasRow: boolean
  /** The folder these files were picked in (a mixed folder is separated into one candidate per person). */
  parent: string
}

export interface CheckResponse {
  plans: ImportPlan[]
  unmatchedRows: { rowNumber: number | null; name?: string; folder?: string }[]
}

export interface ImportResult {
  folder: string
  action: ImportAction
  name?: string
  candidateId?: string
  ref?: string
  order?: string | null
  completedThrough?: number
  currentStage?: number | null
  issues: ImportIssue[]
}

export interface SheetRows {
  rows: Record<string, string | number>[]
  recognised: string[]
  ignored: string[]
}

export interface ImportDefaults {
  order: string
  recruiterId: string
}

const JUNK = /^(\.|~\$|thumbs\.db$|desktop\.ini$)/i
const SHEET = /\.(xlsx|csv)$/i

/** Group the files of a picked folder: one sub-folder per candidate. Files straight inside the picked folder are not candidates. */
export function groupPicked(list: FileList | File[]): PickedFolders {
  const folders = new Map<string, PickedFolder>()
  const loose: File[] = []
  let ignored = 0
  const files = Array.from(list)
  for (const file of files) {
    const relative = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name
    const parts = relative.split('/').filter(Boolean)
    if (parts.some((part) => JUNK.test(part))) { ignored += 1; continue }
    if (parts.length <= 2) { loose.push(file); continue }
    const folder = parts[1]
    const entry = folders.get(folder) ?? { folder, files: [] }
    entry.files.push({ path: parts.slice(2).join('/'), file })
    folders.set(folder, entry)
  }
  const sheets = loose.filter((file) => SHEET.test(file.name))
  const sheet = sheets.find((file) => /sheet|candidate|import|stage|manifest/i.test(file.name)) ?? sheets[0] ?? null
  const rest = loose.filter((file) => !SHEET.test(file.name))
  if (folders.size === 0 && rest.length > 0) {
    // Someone picked a single candidate's folder rather than the folder that holds them all.
    const root = ((rest[0] as File & { webkitRelativePath?: string }).webkitRelativePath || '').split('/')[0] || 'Candidate'
    folders.set(root, { folder: root, files: rest.map((file) => ({ path: file.name, file })) })
  } else {
    ignored += rest.length
  }
  const sorted = Array.from(folders.values()).sort((a, b) => a.folder.localeCompare(b.folder, undefined, { numeric: true }))
  sorted.forEach((entry) => entry.files.sort((a, b) => a.path.localeCompare(b.path, undefined, { numeric: true })))
  return { folders: sorted, sheet, ignored }
}

export const fileTooLarge = (file: File) => file.size > MAX_DOCUMENT_BYTES

export const importApi = {
  readSheet(file: File) {
    const form = new FormData()
    form.append('file', file, file.name)
    return api.form<SheetRows>('/candidate-import/manifest', form)
  },
  check(body: {
    folders: { folder: string; files: { path: string; kind?: string }[] }[]
    rows: unknown[]
    defaultOrder: string
    defaultRecruiterId: string
    jobs: Record<string, string>
  }) {
    return api.post<CheckResponse>('/candidate-import/check', body)
  },
  importOne(picked: PickedFolder, plan: ImportPlan, kinds: Record<string, string>, defaults: ImportDefaults) {
    const wanted = new Set(plan.files.filter((f) => !f.skipped).map((f) => f.path))
    const send = picked.files.filter(({ path, file }) => wanted.has(path) && file.size > 0 && !fileTooLarge(file))
    const form = new FormData()
    form.append('spec', JSON.stringify({
      folder: plan.folder,
      name: plan.name,
      row: plan.row,
      files: send.map(({ path }) => ({ path, ...(kinds[path] ? { kind: kinds[path] } : {}) })),
      defaultOrder: defaults.order || null,
      defaultRecruiterId: defaults.recruiterId || null,
    }))
    send.forEach(({ path, file }) => form.append('files', file, path.split('/').pop() ?? file.name))
    return api.form<ImportResult>('/candidate-import/candidate', form)
  },
  async downloadSheet(folders: string[]) {
    const response = await fetch(fileHref('/api/candidate-import/template'), {
      method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ folders }),
    })
    if (!response.ok) throw new Error('We could not make the sheet. Please try again.')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'candidate-import-sheet.xlsx'
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  },
  readAgain: (candidateIds: string[]) => api.post<{ queued: number }>('/candidate-import/read-again', { candidateIds }),
  kinds: () => api.get<{ kinds: { key: string; label: string }[] }>('/candidate-import/columns'),
}
