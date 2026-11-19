import { fileHref } from '@/shared/api/client'

/**
 * Download a file the API builds (an Excel sheet) with the signed-in session: the response becomes a blob and is saved under `fileName`.
 * A server refusal throws its own sentence; a file that takes longer than `timeoutMs` throws a plain one. `path` is an API path
 * without the /api prefix ("/daily-work/export?format=xlsx").
 */
export async function downloadApiFile(path: string, fileName: string, timeoutMs = 90_000): Promise<void> {
  const controller = new AbortController()
  const deadline = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(fileHref(`/api${path}`), { credentials: 'include', signal: controller.signal })
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
