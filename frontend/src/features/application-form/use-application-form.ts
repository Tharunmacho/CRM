import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError } from '@/shared/api/client'
import { classifyLoadError, type LoadErrorView } from './load-error'
import { liveInterval } from '@/shared/state/live'
import { isOtherRecord } from '@/shared/state/live-events'
import { FORM_LIVE_TOPICS, addFeedback, deleteFeedback, loadApplicationForm, saveApplicationForm, saveAssignment, uploadFormDocument, type FeedbackDraft, type FormTarget } from './form-api'
import { applyAssignmentChange, applyOptimisticUploads, buildSavePayload, changedPaths, completenessOf, isDirty, isReading, rebaseDraft, toDraft, validateDraft } from './form-model'
import type { ApplicationFormData, AssignmentChange, AssignmentData, DocumentKind, FormDraft } from './types'

/** What the screen adds to the assignment block of an older server; it is told who may work the candidate and which job the form shows. */
export type AssignmentCompleter = (assignment: AssignmentData, context: { canEdit: boolean; activeOrderId: string }) => AssignmentData
const contextOf = (form: ApplicationFormData) => ({ canEdit: form.candidate.canEdit, activeOrderId: form.application?.orderId ?? '' })

/** The five fields of the assignment block, for the per-field saving flag and error. */
export type AssignmentField = 'client' | 'job' | 'source' | 'agency' | 'recruiter'

/** Shown when the server does not know the assignment route: it is an older build than this screen. */
export const RESTART_MESSAGE = 'Restart the CRM server to save this change'
export const NOTES_RESTART_MESSAGE = 'Restart the CRM server to save notes'

const messageOf = (cause: unknown, fallback: string): string => (cause instanceof ApiError || cause instanceof Error ? cause.message : fallback)

const POLL_MS = 30_000
const READING_POLL_MS = 4_000
const READING_POLL_MAX_MS = 30_000
/** A burst of live events (a document being read announces page after page) becomes one read. */
const LIVE_WINDOW_MS = 1_200

/**
 * Loads the form, keeps it current (live topics, plus a fast poll while a document is being read), and holds the
 * in-place edit state. A refresh that arrives mid-edit only replaces the fields the person has not touched.
 */
export function useApplicationForm(candidateId: string, target: FormTarget, completeAssignment?: AssignmentCompleter) {
  const { orderId, applicationId } = target
  const [form, setForm] = useState<ApplicationFormData | null>(null)
  const [draft, setDraft] = useState<FormDraft | null>(null)
  const [editing, setEditing] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<LoadErrorView | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [justUpdated, setJustUpdated] = useState<string[]>([])
  /** Files being sent, by slot: shown as uploaded at once, so the checklist and the percentage move before the answer lands. */
  const [pendingUploads, setPendingUploads] = useState<Record<DocumentKind, { fileName: string; sizeKb: number }>>({})
  /** The assignment block with the change being saved already applied. */
  const [assignmentDraft, setAssignmentDraft] = useState<AssignmentData | null>(null)
  const [assignmentSaving, setAssignmentSaving] = useState<Partial<Record<AssignmentField, boolean>>>({})
  const [assignmentErrors, setAssignmentErrors] = useState<Partial<Record<AssignmentField, string>>>({})
  const [feedbackBusy, setFeedbackBusy] = useState(false)
  const [feedbackError, setFeedbackError] = useState('')
  const assignmentInFlight = useRef(0)
  /** Fills in what an older server leaves out of the assignment block (options, permissions); the server's own values win. */
  const completeRef = useRef(completeAssignment)
  completeRef.current = completeAssignment
  const baseRef = useRef<FormDraft | null>(null)
  const draftRef = useRef<FormDraft | null>(null)
  const baseFormRef = useRef<ApplicationFormData | null>(null)
  draftRef.current = draft
  baseFormRef.current = form
  const editingRef = useRef(false)
  editingRef.current = editing
  const savingRef = useRef(false)
  savingRef.current = saving
  const flashTimer = useRef<ReturnType<typeof setTimeout>>()
  const seq = useRef(0)
  const lastSignature = useRef('')
  const quietPolls = useRef(0)

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
      // A background read that finds exactly what is on screen changes nothing (no re-render, no draft rebase).
      const signature = JSON.stringify(next)
      const same = quiet && baseRef.current !== null && signature === lastSignature.current
      lastSignature.current = signature
      quietPolls.current = same ? quietPolls.current + 1 : 0
      if (same) return
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
    lastSignature.current = ''
    quietPolls.current = 0
    setForm(null); setDraft(null); setEditing(false); setError(null); setSaveError('')
    setPendingUploads({}); setAssignmentDraft(null); setAssignmentSaving({}); setAssignmentErrors({}); setFeedbackError('')
    void reload(false)
    return () => { seq.current += 1 }
  }, [reload])

  const reading = form ? isReading(form) : false
  // While a document is being read the form is polled quickly, backing off while nothing changes (a reader that is
  // not running should not be asked every four seconds for as long as the page stays open).
  useEffect(
    () => liveInterval(
      () => (savingRef.current ? undefined : reload(true)),
      () => (reading ? Math.min(READING_POLL_MAX_MS, READING_POLL_MS * 2 ** Math.min(3, quietPolls.current)) : POLL_MS),
      FORM_LIVE_TOPICS,
      {
        debounceMs: LIVE_WINDOW_MS,
        // Edits to other people's records are announced to the whole desk; only this candidate's matter here.
        onChange: (change) => { if (!savingRef.current && !isOtherRecord(change, 'candidates', candidateId)) void reload(true) },
      },
    ),
    [reload, reading, candidateId],
  )
  useEffect(() => () => clearTimeout(flashTimer.current), [])

  const errors = useMemo(() => (draft ? validateDraft(draft) : {}), [draft])
  const dirty = Boolean(baseRef.current && draft && isDirty(baseRef.current, draft))
  /** The form as the person should see it: uploads in flight counted, the assignment change being saved applied. */
  const view = useMemo(() => {
    if (!form) return null
    const documents = applyOptimisticUploads(form.documents, pendingUploads)
    if (documents === form.documents && !assignmentDraft && !completeAssignment) return form
    return { ...form, documents, assignment: assignmentDraft ?? (completeAssignment ? completeAssignment(form.assignment, contextOf(form)) : form.assignment) }
  }, [form, pendingUploads, assignmentDraft, completeAssignment])
  /** Completeness of the form as it stands, so the checklist follows what is typed (and uploaded) before it is saved. */
  const completeness = useMemo(() => (view && draft ? completenessOf(draft, view.documents) : null), [view, draft])

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
      lastSignature.current = JSON.stringify(saved)
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
    setPendingUploads((current) => ({ ...current, [kind]: { fileName: file.name, sizeKb: Math.max(1, Math.round(file.size / 1024)) } }))
    try {
      const answered = await uploadFormDocument(candidateId, kind, file)
      if (answered) {
        lastSignature.current = JSON.stringify(answered)
        apply(answered) // shows "Reading…" at once; the finished reading arrives as a live change
      } else await reload(true)
    } finally {
      setPendingUploads((current) => { const { [kind]: _done, ...rest } = current; return rest })
    }
  }, [candidateId, apply, reload])

  /**
   * Save one change to the assignment block. The block shows the new value at once; the server's answer (the whole form) replaces it,
   * and a refusal puts the old value back and keeps the server's sentence next to the field. Resolves to the new form, or null.
   */
  const changeAssignment = useCallback(async (field: AssignmentField, change: AssignmentChange): Promise<ApplicationFormData | null> => {
    const servedForm = baseFormRef.current
    const current = assignmentDraft ?? (servedForm ? (completeRef.current ? completeRef.current(servedForm.assignment, contextOf(servedForm)) : servedForm.assignment) : null)
    if (!current) return null
    assignmentInFlight.current += 1
    setAssignmentErrors((errors) => ({ ...errors, [field]: '' }))
    setAssignmentSaving((saving) => ({ ...saving, [field]: true }))
    setAssignmentDraft(applyAssignmentChange(current, change))
    try {
      const saved = await saveAssignment(candidateId, change)
      lastSignature.current = JSON.stringify(saved)
      apply(saved)
      return saved
    } catch (cause) {
      // A server that has no such route yet answers 404 or 405: one calm sentence, and the old value is back (the draft is cleared below).
      const outdated = cause instanceof ApiError && (cause.status === 405 || (cause.status === 404 && /^not found$/i.test(cause.message.trim())))
      setAssignmentErrors((errors) => ({ ...errors, [field]: outdated ? RESTART_MESSAGE : messageOf(cause, 'This change could not be saved.') }))
      return null
    } finally {
      assignmentInFlight.current -= 1
      if (assignmentInFlight.current === 0) setAssignmentDraft(null)
      setAssignmentSaving((saving) => ({ ...saving, [field]: false }))
    }
  }, [candidateId, apply, assignmentDraft])

  const sendFeedback = useCallback(async (note: FeedbackDraft): Promise<boolean> => {
    setFeedbackBusy(true); setFeedbackError('')
    try {
      const saved = await addFeedback(candidateId, { ...note, applicationId: note.applicationId ?? baseFormRef.current?.application?.id })
      lastSignature.current = JSON.stringify(saved)
      apply(saved)
      return true
    } catch (cause) {
      // An older server has no notes route: one calm sentence, and the typed text stays in the box (the composer keeps it when this answers false).
      const outdated = cause instanceof ApiError && (cause.status === 405 || (cause.status === 404 && /^not found$/i.test(cause.message.trim())))
      setFeedbackError(outdated ? NOTES_RESTART_MESSAGE : messageOf(cause, 'The note could not be saved.'))
      return false
    } finally {
      setFeedbackBusy(false)
    }
  }, [candidateId, apply])

  const removeFeedback = useCallback(async (feedbackId: string): Promise<void> => {
    setFeedbackError('')
    try {
      const saved = await deleteFeedback(candidateId, feedbackId)
      if (saved) { lastSignature.current = JSON.stringify(saved); apply(saved) } else await reload(true)
    } catch (cause) {
      setFeedbackError(messageOf(cause, 'The note could not be deleted.'))
    }
  }, [candidateId, apply, reload])

  return { form: view, draft, setDraft, editing, setEditing, loading, error, saving, saveError, dirty, errors, justUpdated, save, discard, reload, upload, reading, completeness, changeAssignment, assignmentSaving, assignmentErrors, sendFeedback, removeFeedback, feedbackBusy, feedbackError }
}
