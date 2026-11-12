import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Building2, Eye, Loader2, Printer, Save, X } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { useStore } from '@/shared/state/crm-store'
import { useLiveStatus } from '@/shared/state/live'
import { Button } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { useApplicationForm, type AssignmentCompleter } from '@/features/application-form/use-application-form'
import { completeAssignment } from '@/features/application-form/assignment-fallback'
import { useReferenceAgencies } from '@/features/clients/agency-api'
import { applicationFormHref, visibleWarnings } from '@/features/application-form/form-model'
import { documentSlot } from '@/shared/model/document-slots'
import { documentRowStates, documentRowsProgress } from '@/features/application-form/document-rows'
import { candidateSubtitle, candidateTitle, completeText, sectionTargetId, visibleMissing } from '@/features/application-form/paper-model'
import { ProcessSection } from '@/features/application-form/components/process-section'
import type { DocumentKind, FeedbackData, FormDraft, MissingItem, TrackingRow } from '@/features/application-form/types'
import { FormErrorCard, FormSkeleton } from '@/features/application-form/components/form-states'
import { SectionHeader } from '@/features/application-form/components/section-header'
import { AssignmentBar } from '@/features/application-form/components/assignment-bar'
import { DocumentsSection } from '@/features/application-form/components/documents-section'
import { RecordDownloads } from '@/features/application-form/components/record-downloads'
import { FillView } from '@/features/application-form/fill/fill-view'
import { NotesPanel, StageClockBar, type FeedbackActions } from '@/features/application-form/components/feedback-panel'
import { FormSheet } from '@/features/application-form/components/form-sheet'
import { SidePanel } from '@/features/application-form/components/side-panel'
import type { FormContext } from '@/features/application-form/components/sections'
import '@/styles/application-form.css'
import '@/styles/application-form-v2.css'
import '@/styles/candidate-records.css'

/** Stands in for the notes of a server that has none yet: the panel still opens its composer for people who can work the candidate. */
const EMPTY_FEEDBACK: FeedbackData = { supported: false, items: [], canWrite: false, stageClock: null }

/**
 * The application form, full screen and print-ready: /candidates/:id/application?order=:orderId.
 * The page is one working card (assignment, details, documents, notes) that is typed into; the A4 paper form is a drawer opened with
 * Preview form, and it reads the very same live draft, assignment and documents. Print or save as PDF always prints the paper form.
 * It sits over the app shell (a portal), so the address is linkable.
 */
export default function ApplicationFormPage() {
  const { id = '' } = useParams()
  const [search] = useSearchParams()
  const orderId = search.get('order') || undefined
  const applicationId = search.get('application') || undefined
  const navigate = useNavigate()
  const store = useStore()
  const liveStatus = useLiveStatus()
  const candidate = store.candidates.find((item) => item.id === id)
  const agencies = useReferenceAgencies(true)
  // An older server sends no pickers: they are built from the workspace the screen already holds (the server's own values win).
  const { clients, companies, orders, designations, users, me } = store
  const sourceCandidate = candidate ? { source: candidate.source, sourceDetail: candidate.sourceDetail, ownerId: candidate.ownerId } : undefined
  const fillAssignment: AssignmentCompleter = useCallback((assignment, context) => completeAssignment(assignment, {
    clients, companies, orders, designations, users, candidate: sourceCandidate, agencies: agencies.items, me, ...context,
  }), [clients, companies, orders, designations, users, sourceCandidate?.source, sourceCandidate?.sourceDetail, sourceCandidate?.ownerId, agencies.items, me]) // eslint-disable-line react-hooks/exhaustive-deps
  const state = useApplicationForm(id, { orderId, applicationId }, fillAssignment)
  const { form, draft, setDraft, editing, setEditing, errors, dirty, saving, saveError, justUpdated } = state

  const [leaving, setLeaving] = useState(false)
  const [openStage, setOpenStage] = useState<{ stage: number } | null>(null)
  const [busyKinds, setBusyKinds] = useState<DocumentKind[]>([])
  const [previewOpen, setPreviewOpen] = useState(false)
  const fillRef = useRef<HTMLDivElement>(null)
  const previewButton = useRef<HTMLButtonElement>(null)
  const closePreview = useRef<HTMLButtonElement>(null)

  const canWork = form ? form.candidate.canEdit : false
  const application = form?.application ? store.applications.find((item) => item.id === form.application?.id) : undefined
  // The top bar follows the draft as it is typed: the name, then the reference, client and job.
  const title = candidateTitle(draft ? draft.personal.candidateName : undefined, form?.candidate.name || candidate?.name)
  const name = title.text
  const subtitle = candidateSubtitle(form?.candidate.ref || candidate?.ref, form?.assignment)

  // The overlay covers the shell: lock its scroll and give the print stylesheet a hook to hide it.
  useEffect(() => {
    document.body.classList.add('af-open')
    const title = document.title
    return () => { document.body.classList.remove('af-open'); document.title = title }
  }, [])
  useEffect(() => { document.title = ['Application form', name, subtitle].filter(Boolean).join(' - ') }, [name, subtitle])

  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const close = useCallback(() => {
    if (dirty) { setLeaving(true); return }
    if (window.history.length > 1) navigate(-1)
    else navigate(`/candidates/${id}`, { replace: true })
  }, [dirty, navigate, id])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return
      if (previewOpen) { setPreviewOpen(false); return }
      if (document.querySelector('[role="dialog"][aria-modal="true"], .date-picker-popup, .custom-select-list')) return
      close()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [close, previewOpen])

  // The preview drawer takes focus when it opens and gives it back to its button when it closes.
  useEffect(() => {
    if (!previewOpen) return
    closePreview.current?.focus()
    const button = previewButton.current
    return () => button?.focus({ preventScroll: true })
  }, [previewOpen])

  // The Fill view types into the draft; the paper form only reads the very same draft, so it follows every keystroke.
  // The hook keeps typing safe across a live refresh only while it is told the form is being edited, so it is told so for as long as the person may work.
  useEffect(() => { if (canWork && !editing) setEditing(true) }, [canWork, editing, setEditing])
  const ctx = useMemo((): { fill: FormContext; paper: FormContext } | null => {
    if (!form || !draft) return null
    const shared = { form, draft, errors, updated: justUpdated, setDraft: (update: (current: FormDraft) => FormDraft) => setDraft((current) => (current ? update(current) : current)) }
    return { fill: { ...shared, editing: canWork }, paper: { ...shared, editing: false } }
  }, [form, draft, errors, justUpdated, setDraft, canWork])

  const upload = useCallback(async (kind: DocumentKind, file: File) => {
    setBusyKinds((current) => [...current, kind])
    try {
      await state.upload(kind, file)
      const entry = documentSlot(kind)
      store.notify(`${entry?.label ?? 'Document'} uploaded${entry?.readable ? '. Reading it now' : ''}`)
    } finally {
      setBusyKinds((current) => current.filter((item) => item !== kind))
    }
  }, [state, store])

  const smooth = () => (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth') as ScrollBehavior
  /** Scrolls to the first of these elements that is on screen. */
  const scrollToVisible = useCallback((ids: string[]): boolean => {
    for (const elementId of ids) {
      const element = document.getElementById(elementId)
      if (element && element.getClientRects().length > 0) { element.scrollIntoView({ behavior: smooth(), block: 'start' }); return true }
    }
    return false
  }, [])

  const jump = useCallback((section: number) => { scrollToVisible([sectionTargetId(section)]) }, [scrollToVisible])

  /** "Upload" beside a missing document: bring its row into view and open the file picker straight away (inside the click, so the browser allows it). */
  const uploadNow = useCallback((kind: DocumentKind) => {
    const row = document.querySelector<HTMLElement>(`[data-document="${kind}"]`)
    if (row) row.scrollIntoView({ behavior: smooth(), block: 'center' })
    else jump(5)
    row?.querySelector<HTMLInputElement>('input[type="file"]')?.click()
  }, [jump])

  const openNotes = useCallback(() => {
    document.getElementById('af-notes')?.scrollIntoView({ behavior: smooth(), block: 'start' })
  }, [])

  // A new job means a new application: follow it, so the page keeps showing the candidate's current work.
  const changeAssignment = useCallback(async (field: Parameters<typeof state.changeAssignment>[0], change: Parameters<typeof state.changeAssignment>[1]) => {
    const saved = await state.changeAssignment(field, change)
    if (!saved) return
    const now = saved.application
    const stale = (applicationId && now?.id !== applicationId) || (orderId && now?.orderId !== orderId)
    if (stale) navigate(applicationFormHref(id, null, now?.id ?? null), { replace: true })
  }, [state, applicationId, orderId, navigate, id])

  const feedbackActions: FeedbackActions = useMemo(() => ({
    busy: state.feedbackBusy,
    error: state.feedbackError,
    onSend: state.sendFeedback,
    onDelete: state.removeFeedback,
  }), [state.feedbackBusy, state.feedbackError, state.sendFeedback, state.removeFeedback])

  /**
   * "Fill in" beside a missing item: scroll to the field and put the cursor in it (the passport details are section 2 of the page).
   */
  const focusField = useCallback((item: MissingItem, attempt = 0) => {
    const cell = fillRef.current?.querySelector<HTMLElement>(`[data-field="${item.key}"]`) ?? document.getElementById(sectionTargetId(item.section))
    if (!cell) { if (attempt < 6) window.setTimeout(() => focusField(item, attempt + 1), 80); return }
    cell.scrollIntoView({ behavior: smooth(), block: 'center' })
    cell.querySelector<HTMLElement>('input, textarea, select, button.custom-select-trigger, button.date-picker-trigger')?.focus({ preventScroll: true })
  }, [])
  const fillIn = useCallback((item: MissingItem) => {
    setPreviewOpen(false)
    window.setTimeout(() => focusField(item), 60)
  }, [focusField])

  const openTracking = useCallback((row: TrackingRow) => setOpenStage({ stage: row.stage }), [])

  const completeness = state.completeness ?? form?.completeness ?? { percent: 0, filled: 0, total: 0, missing: [] }
  const feedback = form?.feedback
  // Only what the official form asks for: a server that still lists a removed extra does not put it in the checklist.
  const missing = visibleMissing(completeness.missing)
  const shownWarnings = visibleWarnings(form?.warnings ?? [])
  const documentProgress = form ? documentRowsProgress(documentRowStates(form, canWork).map((row) => row.state)) : { uploaded: 0, total: 0, percent: 0 }

  const sheet = form && ctx ? <FormSheet ctx={ctx.paper} missing={missing} canOpenStage={canWork && Boolean(application)} onOpenStage={openTracking} /> : null

  const body = (
    <div className="af" role="dialog" aria-label={`Application form for ${name}`}>
      <div className="af-bar">
        <button type="button" className="af-bar-back" onClick={close}><ArrowLeft aria-hidden="true" /><span>Back</span></button>
        <div className="af-bar-title">
          <span className={cn('af-bar-name', title.muted && 'is-muted')} title={name}>{name}</span>
          {subtitle && <small title={subtitle}>{subtitle}</small>}
        </div>
        {form && <span className="af-pct tnum" title="How much of the official form and its documents is filled in">{completeText(completeness.percent)}</span>}
        <span className={cn('af-live', liveStatus === 'up' && 'is-up')} title={liveStatus === 'up' ? 'Changes by your colleagues appear here as they happen' : 'Live updates are reconnecting. This page still refreshes every few seconds'}>
          <i aria-hidden="true" />{state.reading ? 'Reading documents…' : liveStatus === 'up' ? 'Live' : 'Reconnecting'}
        </span>
        <div className="af-bar-actions">
          {dirty && <span className="af-unsaved" role="status"><i aria-hidden="true" />Unsaved changes</span>}
          {dirty && (
            <>
              <Button variant="ghost" disabled={saving} onClick={() => { if (!window.confirm('Discard your changes to this form?')) return; state.discard() }} icon={<X />}>Discard</Button>
              <Button variant="primary" disabled={saving} onClick={() => void state.save()} icon={saving ? <Loader2 className="animate-spin" /> : <Save />}>{saving ? 'Saving…' : 'Save changes'}</Button>
            </>
          )}
          <Button ref={previewButton} onClick={() => setPreviewOpen(true)} icon={<Eye />} disabled={!form} aria-haspopup="dialog">Preview form</Button>
          {/* The paper form is always rendered, so this prints it from anywhere. */}
          <Button onClick={() => window.print()} icon={<Printer />} disabled={!form}>Print or save as PDF</Button>
        </div>
      </div>

      <div className="af-scroll">
        {state.loading && !form && !state.error ? (
          <FormSkeleton />
        ) : state.error && !form ? (
          <FormErrorCard error={state.error} onRetry={() => void state.reload(false)} onBack={close} />
        ) : form && ctx ? (
          <div className="af-layout">
            <SidePanel percent={completeness.percent} missing={missing} documents={documentProgress} canEdit={canWork} onUpload={uploadNow} onFill={fillIn} onJump={jump} notes={feedback?.supported || canWork ? { count: feedback?.items.length ?? 0, overdue: Boolean(feedback?.stageClock?.needsReason), onOpen: openNotes } : undefined} />
            <div className="af-main">
              {saveError && <p className="af-banner is-error" role="alert"><span>{saveError}</span></p>}
              {shownWarnings.length > 0 && <ul className="af-banner is-warn" role="status">{shownWarnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}
              {feedback && <StageClockBar feedback={feedback} actions={feedbackActions} canWrite={feedback.canWrite} />}
              {/* The assignment is a card of its own at the very top; it scrolls away with the page. */}
              <div className="af-card af-assign-card" id="af-assignment">
                <SectionHeader compact icon={<Building2 />} id="af-assignment-title" title="Client and recruitment details" />
                <AssignmentBar assignment={form.assignment} saving={state.assignmentSaving} errors={state.assignmentErrors} onChange={(field, change) => void changeAssignment(field, change)} applications={form.applications} activeApplicationId={form.application?.id ?? ''} onSwitchApplication={(applicationId) => navigate(applicationFormHref(id, null, applicationId), { replace: true })} />
              </div>
              {/* One white card for the working area: details, documents, process tracking and notes, split by thin dividers. */}
              <div className="af-card af-work">
                <div className="af-fill-wrap" ref={fillRef}>
                  <FillView ctx={ctx.fill} canWork={canWork} upload={upload} busyKinds={busyKinds} />
                </div>
                <RecordDownloads candidateId={id} applicationId={form.application?.id} recordName={[form.candidate.ref, name].filter(Boolean).join('-')} dirty={dirty} canDownload={canWork} />
                <DocumentsSection form={form} canUpload={canWork} onUpload={upload} busyKinds={busyKinds} />
                <ProcessSection form={form} canOpen={canWork} onOpen={(stage) => setOpenStage({ stage })} />
                <NotesPanel feedback={feedback ?? EMPTY_FEEDBACK} actions={feedbackActions} viewer={{ id: store.me.id, role: store.me.role }} canWork={canWork} />
              </div>
            </div>
          </div>
        ) : null}
      </div>

      {/* The paper form lives in the drawer while it is open and in a print-only place otherwise, so there is only ever one. */}
      {sheet && !previewOpen && <div className="af-print-sheet">{sheet}</div>}
      {previewOpen && (
        <>
          <div className="af-drawer-shade" onClick={() => setPreviewOpen(false)} aria-hidden="true" />
          <aside className="af-drawer" role="dialog" aria-modal="true" aria-label="Preview of the application form">
            <header className="af-drawer-head">
              <div className="af-drawer-title">
                <b>Form preview</b>
                <span>Updates as you type and upload</span>
              </div>
              <Button onClick={() => window.print()} icon={<Printer />}>Print or save as PDF</Button>
              <Button ref={closePreview} variant="ghost" onClick={() => setPreviewOpen(false)} icon={<X />}>Close</Button>
            </header>
            <div className="af-drawer-body">{sheet}</div>
          </aside>
        </>
      )}
      <Dialog
        open={leaving}
        onClose={() => setLeaving(false)}
        title="Leave without saving?"
        description="You have changes on this form that are not saved."
        width="sm"
        footer={<>
          <Button variant="ghost" onClick={() => setLeaving(false)}>Keep editing</Button>
          <Button variant="danger" onClick={() => { setLeaving(false); state.discard(); if (window.history.length > 1) navigate(-1); else navigate(`/candidates/${id}`, { replace: true }) }}>Discard changes</Button>
          <Button variant="primary" onClick={() => { void state.save().then((saved) => { if (saved) { setLeaving(false); if (window.history.length > 1) navigate(-1); else navigate(`/candidates/${id}`, { replace: true }) } }) }}>Save and leave</Button>
        </>}
      >
        <p className="text-sm text-ink-600">Saving keeps what you typed and leaves the form.</p>
      </Dialog>
      <StageModal
        applicationId={openStage && application ? application.id : null}
        open={Boolean(openStage && application)}
        focusStage={openStage?.stage}
        onClose={() => { setOpenStage(null); void state.reload(true) }}
      />
    </div>
  )

  return createPortal(body, document.body)
}
