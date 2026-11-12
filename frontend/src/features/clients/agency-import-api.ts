import { api } from '@/shared/api/client'
import type { AgencyImportOptions, AgencyImportPreview, AgencyImportResult } from './agency-import-model'

function importBody(file: File, options?: Partial<AgencyImportOptions>, previewHash?: string): FormData {
  const body = new FormData()
  body.append('file', file, file.name)
  if (options) {
    if (options.mapping !== undefined) body.append('mapping', JSON.stringify(options.mapping))
    if (options.headerRow !== undefined) body.append('headerRow', String(options.headerRow))
    if (options.sheetName) body.append('sheetName', options.sheetName)
  }
  if (previewHash) body.append('previewHash', previewHash)
  return body
}

export const agencyImportApi = {
  preview: (file: File, options?: Partial<AgencyImportOptions>) => api.form<AgencyImportPreview>('/reference-agencies/import/preview', importBody(file, options)),
  confirm: (file: File, preview: AgencyImportPreview) => api.form<AgencyImportResult>('/reference-agencies/import/confirm', importBody(file, preview, preview.previewHash)),
}
