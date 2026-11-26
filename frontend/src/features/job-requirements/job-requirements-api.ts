import { api, fileHref } from '@/shared/api/client'
import { exportQuery, formFileName, type FieldType, type HistoryEntry, type OptionKind, type RequirementOption, type ListFilters, type RequirementField, type RequirementItem, type RequirementPatch, type RequirementsPage } from './job-requirements-model'

/**
 * The Job requirements API (backend/app/api/routers/job_requirements.py). JSON is camelCase. A blank row is created by POSTing an empty body;
 * a PATCH is partial (details merge by key, custom values by field id). The Ad is a multipart upload (`file`) and `api.form` sends it with the session cookie.
 */
export const jobRequirementsApi = {
  list: (query: string, options?: { signal?: AbortSignal; fresh?: boolean }) => api.get<RequirementsPage>(`/job-requirements?${query}`, options),
  create: (body: RequirementPatch = {}) => api.post<RequirementItem>('/job-requirements', body),
  patch: (id: string, body: RequirementPatch) => api.patch<RequirementItem>(`/job-requirements/${encodeURIComponent(id)}`, body),
  remove: (id: string) => api.del<void>(`/job-requirements/${encodeURIComponent(id)}`),
  uploadAd: (id: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return api.form<RequirementItem>(`/job-requirements/${encodeURIComponent(id)}/ad`, form)
  },
  removeAd: (id: string) => api.del<RequirementItem>(`/job-requirements/${encodeURIComponent(id)}/ad`),
  history: (id: string) => api.get<{ items: HistoryEntry[] }>(`/job-requirements/${encodeURIComponent(id)}/history`, { fresh: true }),
  createField: (label: string, type: FieldType) => api.post<RequirementField>('/job-requirement-fields', { label, type }),
  renameField: (id: string, label: string) => api.patch<RequirementField>(`/job-requirement-fields/${encodeURIComponent(id)}`, { label }),
  deleteField: (id: string) => api.del<void>(`/job-requirement-fields/${encodeURIComponent(id)}`),
  /** The field names staff added to the form (a duplicate name answers 409). */
  createOption: (kind: OptionKind, label: string, standard = false) => api.post<RequirementOption>('/job-requirement-options', { kind, label, standard }),
  updateOption: (id: string, body: { label?: string; standard?: boolean }) => api.patch<RequirementOption>(`/job-requirement-options/${encodeURIComponent(id)}`, body),
  deleteOption: (id: string) => api.del<void>(`/job-requirement-options/${encodeURIComponent(id)}`),
}

/** Fetch a file the server builds (cookie auth) and hand it to the browser as a download; a failure carries the server's message. */
async function downloadFile(path: string, fileName: string): Promise<void> {
  const controller = new AbortController()
  const deadline = window.setTimeout(() => controller.abort(), 90_000)
  try {
    const response = await fetch(fileHref(path), { credentials: 'include', signal: controller.signal })
    if (!response.ok) {
      const failure = await response.json().catch(() => null)
      throw new Error(typeof failure?.detail === 'string' ? failure.detail : 'The file could not be created. Please try again.')
    }
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = fileName
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error) {
    if (controller.signal.aborted) throw new Error('The file took too long to create. Please try again.')
    throw error
  } finally {
    window.clearTimeout(deadline)
  }
}

/** Download the list (with the filters of the screen) as an Excel or CSV file. */
export const downloadRequirements = (filters: ListFilters, format: 'xlsx' | 'csv'): Promise<void> =>
  downloadFile(`/api/job-requirements/export?${exportQuery(filters, format)}`, `job-requirements.${format}`)

/** Download ONE requirement as an Excel file in the client's "Job Requirement Form" layout. */
export const downloadRequirementForm = (id: string, clientName: string): Promise<void> =>
  downloadFile(`/api/job-requirements/${encodeURIComponent(id)}/form`, formFileName(clientName))
