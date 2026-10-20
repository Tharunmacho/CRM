/**
 * API client.
 *
 * Requests carry the session cookie, so a refresh keeps you signed in. Errors are
 * surfaced as a typed exception rather than swallowed — a failed save must be visible,
 * since nothing here autosaves and a silent failure would lose a recruiter's work.
 */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly detail?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }

  /** The record was changed by someone else since it was loaded. */
  get isConflict() {
    return this.status === 409
  }

  get isUnauthorised() {
    return this.status === 401
  }
}

/** Same origin in production, where one container serves the API and the SPA. */
const BASE = import.meta.env.VITE_API_URL ?? ''

/** Where a stored file (`/api/files/:id`) opens: the API's own origin, so the session cookie goes with it. */
export function fileHref(url: string) {
  return `${BASE}${url}`
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}/api${path}`, {
      method,
      credentials: 'include',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'Cannot reach the server. Check your connection and try again.')
  }

  if (res.status === 204) return undefined as T

  const text = await res.text()
  const payload = text ? safeParse(text) : null

  if (!res.ok) {
    const detail = (payload as { detail?: unknown } | null)?.detail
    throw new ApiError(res.status, messageFrom(detail) ?? res.statusText, detail)
  }

  return payload as T
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

function messageFrom(detail: unknown): string | undefined {
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && 'message' in detail) {
    return String((detail as { message: unknown }).message)
  }
  return undefined
}

export interface UploadedFile {
  id: string
  name: string
  sizeKb: number
  version: number
  url: string
  uploadedAt: string
}

async function postForm<T>(path: string, form: FormData, failure: string): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}/api${path}`, { method: 'POST', credentials: 'include', body: form })
  } catch {
    throw new ApiError(0, `Cannot reach the server. ${failure}`)
  }

  const text = await res.text()
  const payload = text ? safeParse(text) : null
  if (!res.ok) {
    const detail = (payload as { detail?: unknown } | null)?.detail
    throw new ApiError(res.status, messageFrom(detail) ?? failure, detail)
  }
  return payload as T
}

/**
 * Files upload the moment they are chosen rather than when the form is submitted, so a
 * lost connection cannot take a passport scan with it.
 */
async function upload(
  entityType: 'client' | 'company' | 'candidate' | 'application',
  entityId: string,
  slot: string,
  file: File,
): Promise<UploadedFile> {
  const form = new FormData()
  form.append('entity_type', entityType)
  form.append('entity_id', entityId)
  form.append('slot', slot)
  form.append('file', file)
  return postForm<UploadedFile>('/files', form, 'The file was not uploaded.')
}

/** What the OCR read, named after the add-candidate form's fields. Absent means unsure. */
export interface OcrFields {
  name?: string
  phone?: string
  whatsapp?: string
  email?: string
  dob?: string
  gender?: string
  nationality?: string
  languages?: string[]
  passportNo?: string
  passportIssue?: string
  passportExpiry?: string
  passportPlace?: string
  designation?: string
  totalExperience?: number
  gulfExperience?: number
  skills?: string[]
}

export interface OcrScan {
  fields: OcrFields
  warnings: string[]
  /** The Veris output itself, saved on the candidate with the form. */
  raw: Record<string, unknown>
}

/** Reads a resume or passport. Nothing is saved; the result only pre-fills the form. */
async function ocr(kind: 'resume' | 'passport', file: File): Promise<OcrScan> {
  const form = new FormData()
  form.append('file', file)
  return postForm<OcrScan>(`/ocr/${kind}`, form, 'The file could not be read.')
}

export const api = {
  upload,
  ocr,
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body),
  patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, body),
  del: <T>(path: string) => request<T>('DELETE', path),
}
