import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError } from '@/shared/api/client'
import { classifyLoadError, type LoadErrorView } from './load-error'
import { liveInterval } from '@/shared/state/live'
import { FORM_LIVE_TOPICS, loadApplicationForm, saveApplicationForm, uploadFormDocument, type FormTarget } from './form-api'
import { buildSavePayload, changedPaths, completenessOf, isDirty, isReading, rebaseDraft, toDraft, validateDraft } from './form-model'
import type { ApplicationFormData, DocumentKind, FormDraft } from './types'

const POLL_MS = 30_000
const READING_POLL_MS = 4_000

/**
 * Loads the form, keeps it current (live topics, plus a fast poll while a document is being read), and holds the
 * in-place edit state. A refresh that arrives mid-edit only replaces the fields the person has not touched.
 */
export function useApplicationForm(candidateId: string, target: FormTarget) {
  const { orderId, applicationId } = target
  const [form, setForm] = useState<ApplicationFormData | null>(null)
  const [draft, setDraft] = useState<FormDraft | null>(null)
  const [editing, setEditing] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<LoadErrorView | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [justUpdated, setJustUpdated] = useState<string[]>([])
  const baseRef = useRef<FormDraft | null>(null)
  const draftRef = useRef<FormDraft | null>(null)
  draftRef.current = draft
  const editingRef = useRef(false)
  editingRef.current = editing
  const savingRef = useRef(false)
  savingRef.current = saving
  const flashTimer = useRef<ReturnType<typeof setTimeout>>()
  const seq = useRef(0)

  const apply = useCallback((next: ApplicationFormData) => {
    const nextBase = toDraft(next)
    const previousBase = baseRef.current
    setForm(next)
    if (previousBase && draftRef.current && editingRef.current && isDirty(previousBase, draftRef.current)) {
      setDraft(rebaseDraft(previousBase, draftRef.current, nextBase))
    } else {
      setDraft(nextBase)
    }
    if (previousBase) {
      const changed = changedPaths(previousBase, nextBase)
      if (changed.length) {
        setJustUpdated(changed)
        clearTimeout(flashTimer.current)
        flashTimer.current = setTimeout(() => setJustUpdated([]), 4_000)
      }
    }
    baseRef.current = nextBase
  }, [])

  const reload = useCallback(async (quiet = true) => {
    const mine = ++seq.current
    if (!quiet) setLoading(true)
    try {
      const next = await loadApplicationForm(candidateId, { orderId, applicationId })
      if (mine !== seq.current) return
      apply(next)
      setError(null)
    } catch (cause) {
      if (mine !== seq.current) return
      if (!quiet || !baseRef.current) setError(classifyLoadError(cause as { status?: number; message?: string }))
    } finally {
      if (mine === seq.current) setLoading(false)
    }
  }, [candidateId, orderId, applicationId, apply])

  useEffect(() => {
    baseRef.current = null
    setForm(null); setDraft(null); setEditing(false); setError(null); setSaveError('')
    void reload(false)
    return () => { seq.current += 1 }
  }, [reload])

  const reading = form ? isReading(form) : false
  useEffect(
    () => liveInterval(() => { if (!savingRef.current) void reload(true) }, reading ? READING_POLL_MS : POLL_MS, FORM_LIVE_TOPICS, { debounceMs: 250 }),
    [reload, reading],
  )
  useEffect(() => () => clearTimeout(flashTimer.current), [])

  const errors = useMemo(() => (draft ? validateDraft(draft) : {}), [draft])
  const dirty = Boolean(baseRef.current && draft && isDirty(baseRef.current, draft))
  /** Completeness of the form as it stands, so the checklist follows what is typed before it is saved. */
  const completeness = useMemo(() => (form && draft ? completenessOf(draft, form.documents) : null), [form, draft])

  const save = useCallback(async (): Promise<boolean> => {
    const base = baseRef.current
    const current = draftRef.current
    if (!base || !current || savingRef.current) return false
    if (Object.keys(validateDraft(current)).length) { setSaveError('Fix the highlighted fields before saving.'); return false }
    const body = buildSavePayload(base, current)
    if (Object.keys(body).length === 0) { setEditing(false); return true }
    setSaving(true); setSaveError('')
    try {
      const saved = await saveApplicationForm(candidateId, { orderId, applicationId }, body)
      baseRef.current = null // the saved copy is the new base; nothing to rebase
      apply(saved)
      setEditing(false)
      return true
    } catch (cause) {
      setSaveError(cause instanceof ApiError || cause instanceof Error ? cause.message : 'The form could not be saved.')
      return false
    } finally {
      setSaving(false)
    }
  }, [candidateId, orderId, applicationId, apply])

  const discard = useCallback(() => {
    if (baseRef.current) setDraft(toDraft({ ...(form as ApplicationFormData) }))
    setSaveError(''); setEditing(false)
  }, [form])

  const upload = useCallback(async (kind: DocumentKind, file: File) => {
    const answered = await uploadFormDocument(candidateId, kind, file)
    if (answered) apply(answered) // shows "Reading…" at once; the finished reading arrives as a live change
    else await reload(true)
  }, [candidateId, apply, reload])

  return { form, draft, setDraft, editing, setEditing, loading, error, saving, saveError, dirty, errors, justUpdated, save, discard, reload, upload, reading, completeness }
}
