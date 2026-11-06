/** Bulk actions and delete for the Lead sheet (POST /api/leads/bulk, DELETE /api/leads/{id}); contract: docs/workflows/lead-sheet-contract.md. */
import { ApiError, api, fileHref } from '@/shared/api/client'
import { fileNameFrom } from './lead-api'
import type { BulkResult } from './lead-bulk-model'

const enc = encodeURIComponent

export const leadBulkApi = {
  run: (body: Record<string, unknown>) => api.post<BulkResult>('/leads/bulk', body),
  remove: (id: string) => api.del<{ deleted: number; id: string }>(`/leads/${enc(id)}`),
}

/** The message and code of a failed bulk call (the server's own words win). */
export function bulkErrorMessage(error: unknown, fallback = 'We could not apply that. Please try again.'): string {
  return error instanceof Error && error.message ? error.message : fallback
}

export const bulkErrorCode = (error: unknown): string | null => {
  const detail = error instanceof ApiError ? error.detail : null
  return detail && typeof detail === 'object' && typeof (detail as { code?: unknown }).code === 'string' ? (detail as { code: string }).code : null
}

/** Export the selected leads as the styled Excel file (a POST, so the answer is a file, not a page). */
export async function downloadSelectedLeads(body: Record<string, unknown>): Promise<number> {
  const response = await fetch(fileHref('/api/leads/bulk'), {
    method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...body, action: 'export' }),
  })
  if (!response.ok) {
    let message = 'We could not make the Excel file. Please try again.'
    try { const data = await response.json(); const detail = data?.detail; message = (typeof detail === 'string' ? detail : detail?.message) || message } catch { /* keep the fallback */ }
    throw new Error(message)
  }
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = fileNameFrom(response.headers.get('content-disposition'), 'lead-sheet-selected.xlsx')
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  return Number(response.headers.get('x-lead-count')) || 0
}
