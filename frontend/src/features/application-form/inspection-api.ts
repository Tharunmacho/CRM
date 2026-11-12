import { ApiError, fileHref } from '@/shared/api/client'
import type { FormTarget } from './form-api'

const path = (id: string) => `/candidates/${encodeURIComponent(id)}`

/** File responses retain authentication and show API errors instead of downloading an error page. */
export async function requestFile(url: string): Promise<Blob> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), 180_000)
  try {
    const response = await fetch(fileHref(`/api${url}`), {
      method: 'GET', credentials: 'include', signal: controller.signal,
    })
    if (!response.ok) {
      const payload = await response.json().catch(() => null)
      const detail = payload?.detail
      throw new ApiError(response.status, typeof detail === 'string' ? detail : Array.isArray(detail)
        ? detail.map((entry: { msg?: string }) => entry.msg).filter(Boolean).join('. ')
        : 'The file could not be prepared. Please try again.')
    }
    return await response.blob()
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw new Error(controller.signal.aborted ? 'Preparing the file took too long. Please try again.' : 'Cannot reach the server. Please try again.')
  } finally { window.clearTimeout(timer) }
}

export function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

export async function downloadCandidateRecord(id: string, target: FormTarget, kind: 'xlsx' | 'zip', recordName?: string) {
  const query = new URLSearchParams()
  if (target.applicationId) query.set('applicationId', target.applicationId)
  else if (target.orderId) query.set('orderId', target.orderId)
  const file = kind === 'xlsx' ? 'inspection.xlsx' : 'documents.zip'
  const blob = await requestFile(`${path(id)}/${file}${query.size ? `?${query}` : ''}`)
  const expected = kind === 'xlsx' ? 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' : 'application/zip'
  if (blob.type.split(';')[0] !== expected) throw new Error('The server did not return the requested file. Refresh the page and try again.')
  const name = (recordName || `candidate-${id}`).replace(/[<>:"/\\|?*\x00-\x1f]/g, '-').replace(/^[.\s]+|[.\s]+$/g, '').slice(0, 140) || 'candidate'
  saveBlob(blob, `${name}-${file}`)
}
