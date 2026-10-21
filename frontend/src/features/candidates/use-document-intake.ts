import { useEffect, useRef, useState } from 'react'
import { api, ApiError, fileHref, type OcrScan } from '@/shared/api/client'
import type { DocumentScans, ScanKind } from './ocr-form'

export type IntakeDraft = { form: Record<string, string>; editedFields: string[]; jobIds?: string[]; savedCandidateId?: string | null }
export type IntakeKind = ScanKind | 'photo' | 'education' | 'experience'
export type IntakeJob = {
  id: string; groupId: string; kind: IntakeKind; filename: string; sizeKb: number; status: string;
  lastError: string | null; candidateId: string | null; attachmentId: string | null;
  originalUrl: string; createdAt: string; updatedAt: string;
  result?: { scans?: DocumentScans }; draft?: IntakeDraft
}
const active = new Set(['pending', 'processing', 'retry'])
const pause = (signal: AbortSignal) => new Promise<void>((resolve, reject) => {
  const cancel = () => { clearTimeout(timer); reject(new DOMException('Stopped waiting', 'AbortError')) }
  const timer = setTimeout(() => { signal.removeEventListener('abort', cancel); resolve() }, 2500)
  signal.addEventListener('abort', cancel, { once: true })
  if (signal.aborted) cancel()
})
export function scansFromJobs(jobs: IntakeJob[]): DocumentScans {
  const scans: DocumentScans = {}
  for (const job of jobs) for (const [kind, scan] of Object.entries(job.result?.scans ?? {})) {
    const key = kind as ScanKind
    if (!scans[key] || Object.keys(scan.raw ?? {}).length > 0) scans[key] = scan as OcrScan
  }
  return scans
}

export function useDocumentIntake(ownerId: string) {
  const group = useRef<string>(crypto.randomUUID())
  const current = useRef<IntakeJob[]>([])
  const [jobs, setJobs] = useState<IntakeJob[]>([])
  const [available, setAvailable] = useState<IntakeJob[]>([])
  const [uploading, setUploading] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [draftError, setDraftError] = useState('')
  const queue = useRef<Promise<unknown>>(Promise.resolve())
  const mounted = useRef(true)
  const update = (value: IntakeJob[]) => { current.current = value; if (mounted.current) setJobs(value) }
  const list = async () => {
    setLoading(true)
    try { const values = await api.get<IntakeJob[]>('/ocr/jobs'); if (mounted.current) { setAvailable(values); setError('') } }
    catch (err) { if (mounted.current) setError(err instanceof Error ? err.message : 'Could not load saved intake') }
    finally { if (mounted.current) setLoading(false) }
  }
  useEffect(() => {
    mounted.current = true
    void list()
    return () => { mounted.current = false }
  }, [ownerId])

  const saveDraft = async (draft: IntakeDraft) => {
    if (!current.current.length) return
    const identifier = group.current
    const payload = { ...draft, jobIds: current.current.map((job) => job.id) }
    const work = queue.current.catch(() => {}).then(() => api.put('/ocr/jobs/groups/' + identifier + '/draft', payload))
    queue.current = work
    try { await work; if (mounted.current) setDraftError('') }
    catch (err) { if (mounted.current) setDraftError('Your latest form changes could not be backed up. Keep this page open and retry saving the draft.'); throw err }
  }
  const upload = async (kind: IntakeKind, file: File) => {
    setUploading(true); setError('')
    const form = new FormData()
    form.append('kind', kind); form.append('group_id', group.current); form.append('file', file)
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 120000)
    try {
      const response = await fetch(fileHref('/api/ocr/jobs'), { method: 'POST', credentials: 'include', body: form, signal: controller.signal })
      const payload = await response.json()
      if (!response.ok) throw new ApiError(response.status, typeof payload.detail === 'string' ? payload.detail : 'The original could not be saved. Please retry.')
      const job = payload as IntakeJob
      const previous = current.current.find((value) => value.kind === kind)
      update([...current.current.filter((value) => value.kind !== kind), job])
      if (previous && previous.id !== job.id) await api.post('/ocr/jobs/' + previous.id + '/dismiss').catch(() => {})
      return job
    } catch (err) {
      const message = err instanceof Error && err.name !== 'AbortError' ? err.message : 'The upload could not finish. Keep the original and retry.'
      if (mounted.current) setError(message)
      throw new Error(message)
    } finally { clearTimeout(timeout); if (mounted.current) setUploading(false) }
  }
  const recover = async (identifier: string) => {
    setLoading(true); setError('')
    try {
      const all = await api.get<IntakeJob[]>('/ocr/jobs/groups/' + identifier)
      const draft = [...all].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)).find((job) => job.draft?.form)?.draft
      const selected = draft?.jobIds?.length ? all.filter((job) => draft.jobIds!.includes(job.id)) : all.filter((job) => job.status !== 'dismissed')
      const byKind = new Map<IntakeKind, IntakeJob>()
      for (const job of selected) byKind.set(job.kind, job)
      group.current = identifier
      update([...byKind.values()])
      return { jobs: current.current, draft }
    } catch (err) { setError(err instanceof Error ? err.message : 'Saved intake could not be opened'); throw err }
    finally { setLoading(false) }
  }
  const read = async (signal: AbortSignal) => {
    const identifiers = current.current.map((job) => job.id)
    for (const job of current.current) {
      if (['resume', 'passport', 'aadhaar'].includes(job.kind) && ['uploaded', 'failed', 'needs_review', 'dismissed'].includes(job.status)) await api.post('/ocr/jobs/' + job.id + '/retry')
    }
    const stopAt = Date.now() + 20 * 60000
    while (!signal.aborted) {
      const values = (await api.get<IntakeJob[]>('/ocr/jobs/groups/' + group.current, { signal })).filter((job) => identifiers.includes(job.id))
      update(values)
      if (!values.some((job) => active.has(job.status))) return values
      if (Date.now() > stopAt) throw new Error('Reading is still in progress. Your originals and progress are saved; resume this intake later.')
      await pause(signal)
    }
    throw new DOMException('Stopped waiting', 'AbortError')
  }
  const attach = async (candidateId: string) => {
    const failures: Record<string, string> = {}
    for (const job of current.current) {
      if (job.candidateId === candidateId && job.status === 'attached') continue
      try {
        const saved = await api.post<IntakeJob>('/ocr/jobs/' + job.id + '/attach', { candidateId })
        update(current.current.map((value) => value.id === saved.id ? saved : value))
      } catch (err) { failures[job.kind] = job.filename + ': ' + (err instanceof Error ? err.message : 'Saved document could not be attached') }
    }
    return failures
  }
  const dismiss = async (identifier: string) => {
    const selected = available.filter((job) => job.groupId === identifier)
    await Promise.all(selected.map((job) => api.post('/ocr/jobs/' + job.id + '/dismiss')))
    await list()
  }
  return { jobs, available, uploading, loading, error, draftError, upload, recover, read, attach, saveDraft, reload: list, dismiss }
}
