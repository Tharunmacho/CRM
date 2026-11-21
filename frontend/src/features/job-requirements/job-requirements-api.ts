import { api, fileHref } from '@/shared/api/client'
import { exportQuery, type FieldType, type HistoryEntry, type ListFilters, type RequirementField, type RequirementItem, type RequirementPatch, type RequirementsPage } from './job-requirements-model'

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
}

/** Download the list (with the filters of the screen) as an Excel or CSV file. */
export async function downloadRequirements(filters: ListFilters, format: 'xlsx' | 'csv'): Promise<void> {
  const controller = new AbortController()
  const deadline = window.setTimeout(() => controller.abort(), 90_000)
  try {
    const response = await fetch(fileHref(`/api/job-requirements/export?${exportQuery(filters, format)}`), { credentials: 'include', signal: controller.signal })
    if (!response.ok) {
      const failure = await response.json().catch(() => null)
      throw new Error(typeof failure?.detail === 'string' ? failure.detail : 'The file could not be created. Please try again.')
    }
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = `job-requirements.${format}`
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
