/**
 * The workspace snapshot read, with "has it changed?" built in.
 *
 * The server stamps the compact snapshot with a `revision` (also its ETag), a digest of the whole body. Sending that
 * back as If-None-Match turns a re-read of an unchanged workspace into an empty 304: the caller keeps what it holds. The
 * revision is of the content, not of a clock, so a 304 only ever means "byte for byte what you already have", and it is
 * different for every signed-in person (their data is in it). Nothing is stored by the browser: the snapshot's
 * Cache-Control stays `private, no-store`, and the revision lives only in memory, next to the data it describes.
 *
 * This stays separate from `client.ts`, whose shared reads do not expose response status or request headers.
 */
import { ApiError, fileHref } from './client'

export type DeskAnswer<T> = { same: true } | { same: false; payload: T }

const TIMEOUT_MS = 30_000

function messageOf(detail: unknown, fallback: string): string {
  if (typeof detail === 'string' && detail) return detail
  const message = (detail as { message?: unknown } | null)?.message
  return typeof message === 'string' && message ? message : fallback
}

/** GET `path` (under /api); `revision` is the one the caller's copy came with, if any. */
export async function readDesk<T>(path: string, options: { signal?: AbortSignal; revision?: string } = {}): Promise<DeskAnswer<T>> {
  const controller = new AbortController()
  const timeout = { fired: false }
  const timer = setTimeout(() => { timeout.fired = true; controller.abort() }, TIMEOUT_MS)
  const cancel = () => controller.abort()
  if (options.signal?.aborted) controller.abort()
  else options.signal?.addEventListener('abort', cancel, { once: true })
  try {
    const res = await fetch(fileHref(`/api${path}`), {
      credentials: 'include',
      headers: options.revision ? { 'If-None-Match': `"${options.revision}"` } : undefined,
      signal: controller.signal,
    })
    if (res.status === 304) return { same: true }
    const text = await res.text()
    let parsed: unknown = null
    try { parsed = text ? JSON.parse(text) : null } catch { parsed = null }
    if (!res.ok) {
      const detail = (parsed as { detail?: unknown } | null)?.detail
      throw new ApiError(res.status, messageOf(detail, res.statusText), detail)
    }
    if (parsed === null || typeof parsed !== 'object') throw new ApiError(502, 'The server returned an unexpected response. Please try again.')
    return { same: false, payload: parsed as T }
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (options.signal?.aborted && !timeout.fired) throw error // cancelled by the caller, not a failure
    throw new ApiError(0, timeout.fired
      ? 'The server took too long to respond. Please try again.'
      : 'Cannot reach the server. Check your connection and try again.')
  } finally {
    clearTimeout(timer)
    options.signal?.removeEventListener('abort', cancel)
  }
}
