import type { Application, FileVersion } from '@/shared/model/types'

export interface ApplicationFile extends FileVersion { slot: string }

export function applicationFileSlot(stage: number, field: string): string {
  return `stage_${stage}_${field}`
}

/** Older stages reused `file`; only known references can safely assign those uploads. */
export function applicationFileVersions(files: ApplicationFile[], app: Application, stage: number, field: string): FileVersion[] {
  const records = app.stages.filter((record) => record.stage === stage).map((record) => record.data)
  if (app.currentStage === stage) records.push(app.draft)
  const referenced = new Set(records.map((data) => (data[field] as { id?: string } | undefined)?.id).filter(Boolean))
  return files.filter((file) => file.slot === applicationFileSlot(stage, field) || referenced.has(file.id))
}

export const DOCUMENT_ACCEPT = '.pdf,.doc,.docx,.jpg,.jpeg,.png,.webp,.heic'
export const DOCUMENT_HELP = 'PDF, Word, JPG, PNG, WebP or HEIC. Up to 25 MB.'
export const MAX_DOCUMENT_BYTES = 25 * 1024 * 1024

/** Browser validation is an early hint; the server still validates every upload. */
export function documentFileError(file: Pick<File, 'name' | 'size'>): string | undefined {
  if (file.size === 0) return 'This file is empty. Choose a file with content.'
  if (file.size > MAX_DOCUMENT_BYTES) return 'This file is too large. Choose a file of 25 MB or less.'
  if (!/\.(pdf|docx?|jpe?g|png|webp|heic)$/i.test(file.name)) {
    return 'Choose a PDF, Word document, JPG, PNG, WebP or HEIC image.'
  }
  return undefined
}

/** A list may arrive in any order. Version numbers decide which upload is current. */
export function newestFileVersions(versions: FileVersion[]): FileVersion[] {
  return [...versions].sort((a, b) => b.version - a.version || b.uploadedAt.localeCompare(a.uploadedAt))
}

export function fileDownloadPath(url: string): string {
  const [path, fragment] = url.split('#', 2)
  const [pathname, query] = path.split('?', 2)
  const params = new URLSearchParams(query)
  params.set('download', 'true')
  return `${pathname}?${params.toString()}${fragment === undefined ? '' : `#${fragment}`}`
}
