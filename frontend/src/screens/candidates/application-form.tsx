import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { AlertTriangle, ArrowLeft, Building2, Eye, Loader2, Printer, Save, X } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { useStore } from '@/shared/state/crm-store'
import { refreshSilently } from '@/shared/state/refresh-retry'
import { useLiveStatus } from '@/shared/state/live'
import { Button } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { UnmapCandidateDialog } from '@/features/jobs/components/job-dialogs'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { useApplicationForm, type AssignmentCompleter } from '@/features/application-form/use-application-form'
import { completeAssignment } from '@/features/application-form/assignment-fallback'
import { useReferenceAgencies } from '@/features/clients/agency-api'
import { COMMENCEMENT_PATH, applicationFormHref, pickersLocked, visibleWarnings } from '@/features/application-form/form-model'
import { documentLabel, documentSlot } from '@/shared/model/document-slots'
import { documentRowStates, documentRowsProgress } from '@/features/application-form/document-rows'
import { candidateSubtitle, candidateTitle, completeText, visibleMissing } from '@/features/application-form/paper-model'
import { DEFAULT_TAB, availableTabs, hashForTab, isDetailTab, missingByTab, tabForMissing, tabForSection, tabFromHash, visibleCards, type FormTab } from '@/features/application-form/form-tabs'
import { FormTabs, type TabBadge } from '@/features/application-form/components/form-tabs'
import { ContextStrip } from '@/features/application-form/components/context-strip'
import { RecruitmentStages, StageControl } from '@/features/application-form/components/recruitment-stages'
import { PrescreeningPanel } from '@/features/application-form/components/prescreening-panel'
import type { PrescreenState } from '@/features/application-form/prescreening'
import type { DocumentKind, FeedbackData, FormDraft, MissingItem, TrackingRow } from '@/features/application-form/types'
import { FormErrorCard, FormSkeleton } from '@/features/application-form/components/form-states'
import { SectionHeader } from '@/features/application-form/components/section-header'
import { AssignmentBar } from '@/features/application-form/components/assignment-bar'
import { CommencementField } from '@/features/application-form/components/commencement-field'
import { DocumentsSection } from '@/features/application-form/components/documents-section'
import { MergeIntoPicker } from '@/features/application-form/components/candidate-merge'
import { SHOW_MERGE_UI } from '@/features/application-form/duplicate-merge'
import { RecordDownloads } from '@/features/application-form/components/record-downloads'
import { InvoiceCard } from '@/features/application-form/components/invoice-card'
import { FillView } from '@/features/application-form/fill/fill-view'
import { NotesPanel, StageClockBar, type FeedbackActions } from '@/features/application-form/components/feedback-panel'
import { FormSheet } from '@/features/application-form/components/form-sheet'
import { SidePanel } from '@/features/application-form/components/side-panel'
import type { FormContext } from '@/features/application-form/components/sections'
import '@/styles/application-form.css'
import { careIdOf, primaryId } from '@/features/candidates/candidate-ids'
import '@/styles/application-form-v2.css'
import '@/styles/candidate-records.css'
import '@/styles/application-stages.css'
import '@/styles/application-form-tabs.css'
import { isAdminLevel } from '@/shared/model/role-labels'

const missingLabel = (item: MissingItem): string => (item.kind === 'document' ? `${documentLabel(item.key.replace('documents.', ''))} not uploaded` : item.label)

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
  const workspaceReads = useRef(store.ensureFresh)
  workspaceReads.current = store.ensureFresh
  useEffect(() => { void workspaceReads.current().catch(() => { /* The form's authorized options remain usable when the workspace refresh fails. */ }) }, [id, store.me.id])
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
  const [prescreenState, setPrescreenState] = useState<PrescreenState>('not_started')   // what the Prescreening panel last read (the stage list beside it shows the same)
  const [stageDirty, setStageDirty] = useState(false)
  const [stageBusy, setStageBusy] = useState(false)
  const [busyKinds, setBusyKinds] = useState<DocumentKind[]>([])
  const [previewOpen, setPreviewOpen] = useState(false)
  const [unmapOpen, setUnmapOpen] = useState(false)
  const [unmapSaving, setUnmapSaving] = useState(false)
  const [unmapError, setUnmapError] = useState('')
  const fillRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  // One section at a time under the sticky tab bar; a link such as #documents opens that section.
  const [tab, setTab] = useState<FormTab>(() => tabFromHash(window.location.hash) ?? DEFAULT_TAB)
  const previewButton = useRef<HTMLButtonElement>(null)
  const closePreview = useRef<HTMLButtonElement>(null)

  const canWork = form ? form.candidate.canEdit : false
  const application = form?.application
  const selectedStage = application ? openStage?.stage ?? application.currentStage : null
  useEffect(() => { setOpenStage(null); setStageDirty(false); setStageBusy(false) }, [application?.id])
  const leaveStage = useCallback(() => !stageBusy && (!stageDirty || window.confirm('Discard the stage feedback you have not saved?')), [stageBusy, stageDirty])
  const selectStage = useCallback((stage: number) => {
    if (stage === selectedStage || !leaveStage()) return
    setStageDirty(false)
    setOpenStage({ stage })
  }, [selectedStage, leaveStage])
  // The top bar follows the draft as it is typed: the name, then the reference, client and job.
  const title = candidateTitle(draft ? draft.personal.candidateName : undefined, form?.candidate.name || candidate?.name)
  const name = title.text
  // ONE ID in the header: the Data Bank ID, then the client and job. The CRM reference only stands in for a row with no Data Bank ID; no Traveller ID here.
  const ids = { ref: form?.candidate.ref || candidate?.ref, careId: form?.candidate.careId || candidate?.careId }
  const idText = careIdOf(ids)
  const subline = candidateSubtitle(careIdOf(ids) ? '' : ids.ref, form?.assignment)
  const subtitle = [idText, subline].filter(Boolean).join(' · ')

  // The overlay covers the shell: lock its scroll and give the print stylesheet a hook to hide it.
  useEffect(() => {
    document.body.classList.add('af-open')
    const title = document.title
    return () => { document.body.classList.remove('af-open'); document.title = title }
  }, [])
  useEffect(() => { document.title = ['Application form', name, subtitle].filter(Boolean).join(' - ') }, [name, subtitle])

  useEffect(() => {
    if (!dirty && !stageDirty) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty, stageDirty])

  const close = useCallback(() => {
    if (!leaveStage()) return
    if (dirty) { setLeaving(true); return }
    if (window.history.length > 1) navigate(-1)
    else navigate(`/candidates/${id}`, { replace: true })
  }, [dirty, navigate, id, leaveStage])

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
      const split = await state.upload(kind, file)
      const entry = documentSlot(kind)
      // A scan of several documents was filed field by field: say which went where instead of only "uploaded".
      store.notify(split?.message || `${entry?.label ?? 'Document'} uploaded`)
    } finally {
      setBusyKinds((current) => current.filter((item) => item !== kind))
    }
  }, [state, store])

  /** Remove one uploaded file (soft delete on the server); the slot shows its previous version, or nothing, at once. */
  const removeDocument = useCallback(async (attachmentId: string) => {
    await state.removeDocument(attachmentId)
    store.notify('Document removed')
  }, [state, store])

  const smooth = () => (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth') as ScrollBehavior

  /** Shows a section. The panels stay mounted (nothing typed is lost); the page keeps its place and the section starts under the tab bar. */
  const selectTab = useCallback((next: FormTab, options?: { keepScroll?: boolean }) => {
    setTab(next)
    try { window.history.replaceState(window.history.state, '', `${window.location.pathname}${window.location.search}${hashForTab(next)}`) } catch { /* The address is only a convenience. */ }
    const scroller = scrollRef.current
    const bar = scroller?.querySelector<HTMLElement>('.af-tabs')
    if (!options?.keepScroll && scroller && bar && scroller.scrollTop > bar.offsetTop) scroller.scrollTop = bar.offsetTop
  }, [])
  useEffect(() => {
    const onHash = () => { const linked = tabFromHash(window.location.hash); if (linked) setTab(linked) }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  /** Brings an element of the chosen section into view once it is on screen (a section that was hidden has just been shown). */
  const revealLater = useCallback((find: () => HTMLElement | null | undefined, block: ScrollLogicalPosition = 'center', attempt = 0) => {
    const element = find()
    if (!element || element.getClientRects().length === 0) { if (attempt < 6) window.setTimeout(() => revealLater(find, block, attempt + 1), 60); return }
    element.scrollIntoView({ behavior: smooth(), block })
  }, [])

  const jump = useCallback((section: number) => { selectTab(tabForSection(section)) }, [selectTab])

  /** "Upload" beside a missing document: show Documents and open the file picker straight away (inside the click, so the browser allows it). */
  const uploadNow = useCallback((kind: DocumentKind) => {
    const row = document.querySelector<HTMLElement>(`[data-document="${kind}"]`)
    if (!row) { jump(5); return }
    row.querySelector<HTMLInputElement>('input[type="file"]')?.click()
    selectTab('documents', { keepScroll: true })
    revealLater(() => document.querySelector<HTMLElement>(`[data-document="${kind}"]`))
  }, [jump, selectTab, revealLater])

  const openNotes = useCallback(() => { selectTab('notes') }, [selectTab])

  // A new job means a new application: follow it, so the page keeps showing the candidate's current work.
  const changeAssignment = useCallback(async (field: Parameters<typeof state.changeAssignment>[0], change: Parameters<typeof state.changeAssignment>[1]) => {
    if (field === 'job' && !leaveStage()) return
    const saved = await state.changeAssignment(field, change)
    if (!saved) return
    if (field === 'job' || field === 'recruiter') {
      // The new placement is on the server already; the stage panel shows "Loading the stages…" and re-reads by itself until it appears.
      // Nothing here blocks the person or asks for a manual Refresh: a failed re-read is retried quietly in the background.
      void refreshSilently(() => store.refresh({ keepCurrent: true }))
    }
    const now = saved.application
    const stale = (applicationId && now?.id !== applicationId) || (orderId && now?.orderId !== orderId)
    if (stale) navigate(applicationFormHref(id, null, now?.id ?? null), { replace: true })
  }, [state, applicationId, orderId, navigate, id, leaveStage, store])

  const feedbackActions: FeedbackActions = useMemo(() => ({
    busy: state.feedbackBusy,
    error: state.feedbackError,
    onSend: state.sendFeedback,
    onEdit: state.changeFeedback,
    onDelete: state.removeFeedback,
  }), [state.feedbackBusy, state.feedbackError, state.sendFeedback, state.changeFeedback, state.removeFeedback])

  /**
   * "Fill in" beside a missing item: show the section that holds the field, then scroll to it and put the cursor in it.
   */
  const focusField = useCallback((item: MissingItem, attempt = 0) => {
    const cell = fillRef.current?.querySelector<HTMLElement>(`[data-field="${item.key}"]`)
    if (!cell || cell.getClientRects().length === 0) { if (attempt < 6) window.setTimeout(() => focusField(item, attempt + 1), 80); return }
    cell.scrollIntoView({ behavior: smooth(), block: 'center' })
    cell.querySelector<HTMLElement>('input, textarea, select, button.custom-select-trigger, button.date-picker-trigger')?.focus({ preventScroll: true })
  }, [])
  const fillIn = useCallback((item: MissingItem) => {
    setPreviewOpen(false)
    selectTab(tabForMissing(item), { keepScroll: true })
    window.setTimeout(() => focusField(item), 60)
  }, [focusField, selectTab])

  const openTracking = useCallback((row: TrackingRow) => { selectStage(row.stage); setPreviewOpen(false); selectTab('overview') }, [selectStage, selectTab])

  const completeness = state.completeness ?? form?.completeness ?? { percent: 0, filled: 0, total: 0, missing: [] }
  const feedback = form?.feedback
  // Only what the official form asks for: a server that still lists a removed extra does not put it in the checklist.
  const missing = visibleMissing(completeness.missing)
  const shownWarnings = visibleWarnings(form?.warnings ?? [])
  const documentProgress = form ? documentRowsProgress(documentRowStates(form, canWork).map((row) => row.state)) : { uploaded: 0, total: 0, percent: 0 }

  const canSeeInvoice = store.can('view_invoices')
  const canSeeNotes = Boolean(feedback?.supported || canWork)
  const tabs = availableTabs({ invoice: canSeeInvoice, notes: canSeeNotes })
  const activeTab: FormTab = tabs.includes(tab) ? tab : DEFAULT_TAB
  const shownCards = visibleCards(activeTab)
  const badges = useMemo(() => {
    const out: Partial<Record<FormTab, TabBadge>> = {}
    const counts = missingByTab(missing)
    // A section with nothing left to add shows a check; one with fields to add shows how many.
    for (const key of ['personal', 'passport', 'education', 'experience', 'documents'] as FormTab[]) if (!counts[key]) out[key] = { count: 0, kind: 'done' }
    for (const [key, count] of Object.entries(counts)) out[key as FormTab] = { count: count ?? 0, kind: 'missing' }
    const noteCount = feedback?.items.length ?? 0
    if (feedback?.stageClock?.needsReason) out.notes = { count: noteCount, kind: 'overdue' }
    else if (noteCount > 0) out.notes = { count: noteCount, kind: 'notes' }
    return out
  }, [missing, feedback])
  const here = (id: FormTab) => activeTab === id

  const sheet = form && ctx ? <FormSheet ctx={ctx.paper} missing={missing} canOpenStage={canWork && Boolean(application)} onOpenStage={openTracking} /> : null

  const body = (
    <div className={cn('af', dirty && 'has-actionbar')} role="dialog" aria-label={`Application form for ${name}`}>
      <header className="af-bar">
        <button type="button" className="af-bar-back" onClick={close}><ArrowLeft aria-hidden="true" /><span>Back</span></button>
        <div className="af-bar-title">
          <span className="af-bar-nameline">
            <h1 className={cn('af-bar-name', title.muted && 'is-muted')}>{name}</h1>
            {idText && <span className="af-bar-id tnum" title="Data Bank ID">{idText}</span>}
          </span>
          {subline && <small className="af-bar-sub">{subline}</small>}
        </div>
        <div className="af-bar-status" role="group" aria-label="Form status">
          {form && <span className="af-pct tnum" title="How much of the official form and its documents is filled in">{completeText(completeness.percent)}</span>}
          <span className={cn('af-live', liveStatus === 'up' ? 'is-up' : 'is-retry')} role="status" title={liveStatus === 'up' ? 'Live: changes by your colleagues appear here as they happen' : 'Live updates reconnecting; your edits are safe. This page still refreshes every few seconds'}>
            <i aria-hidden="true" />{state.reading ? 'Reading documents…' : liveStatus === 'up' ? 'Live' : 'Reconnecting'}
          </span>
        </div>
        <div className="af-bar-actions">
          {SHOW_MERGE_UI && form && isAdminLevel(me.role) && <MergeIntoPicker sourceId={id} sourceLabel={primaryId(ids) || name} />}
          <Button ref={previewButton} onClick={() => setPreviewOpen(true)} icon={<Eye />} disabled={!form} aria-haspopup="dialog">Preview form</Button>
          {/* The paper form is always rendered, so this prints it from anywhere. */}
          <Button onClick={() => window.print()} icon={<Printer />} disabled={!form}>Print or save as PDF</Button>
        </div>
      </header>

      <div className="af-scroll" ref={scrollRef}>
        {state.loading && !form && !state.error ? (
          <FormSkeleton />
        ) : state.error && !form ? (
          <FormErrorCard error={state.error} onRetry={() => void state.reload(false)} onBack={close} />
        ) : form && ctx ? (
          <div className="af-layout">
            <SidePanel percent={completeness.percent} missing={missing} documents={documentProgress} canEdit={canWork} onUpload={uploadNow} onFill={fillIn} />
            <div className="af-main">
              {saveError && <p className="af-banner is-error" role="alert"><span>{saveError}</span></p>}
              {state.reading && <p className="af-banner is-reading" role="status"><Loader2 className="animate-spin" aria-hidden="true" /><span>Reading documents… The details fill in by themselves as each file is read.</span></p>}
              {shownWarnings.length > 0 && <ul className="af-banner is-warn" role="status">{shownWarnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}
              {feedback && <StageClockBar feedback={feedback} actions={feedbackActions} canWrite={feedback.canWrite} />}
              <ContextStrip form={form} clock={feedback?.supported ? feedback.stageClock : null} commencement={draft?.extras[COMMENCEMENT_PATH]} onOpen={() => selectTab('overview')} />
              <FormTabs tabs={tabs} active={activeTab} badges={badges} onSelect={(next) => selectTab(next)} />
              {/* Every panel stays mounted (hidden when another section is chosen), so typing, uploads and readings carry on while a recruiter moves between sections. */}
              <div className="af-pane" id="af-pane-overview" role="tabpanel" aria-labelledby="af-tab-overview" hidden={!here('overview')}>
                <div className="af-card af-assign-card" id="af-assignment">
                  <SectionHeader compact icon={<Building2 />} id="af-assignment-title" title="Client and recruitment details" />
                  <AssignmentBar assignment={form.assignment} saving={state.assignmentSaving} errors={state.assignmentErrors} onChange={(field, change) => void changeAssignment(field, change)} applications={form.applications} activeApplicationId={form.application?.id ?? ''} canManageJobs={isAdminLevel(me.role)} pickersLocked={pickersLocked(application?.currentStage, application?.status)} onUnmap={canWork && store.can('map_candidates') && application ? () => { if (!leaveStage()) return; setUnmapError(''); setUnmapOpen(true) } : undefined} onSwitchApplication={(applicationId) => { if (leaveStage()) navigate(applicationFormHref(id, null, applicationId), { replace: true }) }}
                    stageControl={<><StageControl form={form} canOpen={Boolean(application) && !stageBusy} onOpen={selectStage} /><CommencementField ctx={ctx.fill} canWork={canWork} /></>} />
                  <div className="af-stage-workbench">
                  <RecruitmentStages form={form} canOpen={Boolean(application) && !stageBusy} selectedStage={selectedStage} onOpen={selectStage} prescreenOpen={!application} prescreenState={prescreenState} />
                  {application && selectedStage && <div className="af-stage-feedback" id="af-stage-feedback">
                    <StageModal key={`${application.id}-${selectedStage}`} embedded applicationId={application.id} open focusStage={selectedStage}
                      onDirtyChange={setStageDirty} onBusyChange={setStageBusy}
                      onStageChanged={() => { setStageDirty(false); setOpenStage(null); void state.reload(true) }}
                      onClose={() => { if (leaveStage()) { setOpenStage(null); void state.reload(true) } }} />
                  </div>}
                  {/* No job yet: Prescreening (stage 1) is open without a client or job; a client and a job are mandatory from stage 2. */}
                  {!application && <PrescreeningPanel candidateId={id} canWork={canWork} clients={form.assignment.options.clients}
                    onState={setPrescreenState}
                    onChanged={() => { void state.reload(true); void refreshSilently(() => store.refresh({ keepCurrent: true })) }} />}
                  </div>
                </div>
              </div>
              <div className="af-pane af-card af-work" id="af-pane-details" role="tabpanel" aria-labelledby={`af-tab-${isDetailTab(activeTab) ? activeTab : 'personal'}`} hidden={!isDetailTab(activeTab)}>
                <div className="af-fill-wrap" ref={fillRef}>
                  <FillView ctx={ctx.fill} canWork={canWork} upload={upload} onRemove={removeDocument} busyKinds={busyKinds} onExtract={form.capabilities?.deferredDocumentReading ? state.extractDocuments : undefined} readError={state.readError} cards={shownCards} />
                </div>
              </div>
              <div className="af-pane af-card af-work" id="af-pane-documents" role="tabpanel" aria-labelledby="af-tab-documents" hidden={!here('documents')}>
                <DocumentsSection form={form} canUpload={canWork} onUpload={upload} onRemove={removeDocument} busyKinds={busyKinds} onManualStatus={state.markDocument} onIdentity={state.checkIdentity} onExtract={form.capabilities?.deferredDocumentReading ? state.extractDocuments : undefined} readError={state.readError} />
                <RecordDownloads candidateId={id} applicationId={form.application?.id} recordName={[primaryId(ids), name].filter(Boolean).join('-')} dirty={dirty} canDownload={canWork} />
              </div>
              {canSeeInvoice && (
                <div className="af-pane af-card af-work" id="af-pane-invoice" role="tabpanel" aria-labelledby="af-tab-invoice" hidden={!here('invoice')}>
                  <InvoiceCard candidateId={id} applicationId={form.application?.id} canWork={canWork} canConfigure={isAdminLevel(store.me.role)} onChanged={() => void state.reload(true)} />
                </div>
              )}
              {canSeeNotes && (
                <div className="af-pane af-card af-work" id="af-pane-notes" role="tabpanel" aria-labelledby="af-tab-notes" hidden={!here('notes')}>
                  <NotesPanel feedback={feedback ?? EMPTY_FEEDBACK} actions={feedbackActions} viewer={{ id: store.me.id, role: store.me.role }} canWork={canWork} />
                </div>
              )}
            </div>
          </div>
        ) : null}
      </div>

      {/* Unsaved edits: one sticky bar over the bottom edge (it overlays, so nothing above it moves). */}
      {dirty && form && (
        <div className="af-actionbar" role="region" aria-label="Unsaved changes">
          <div className="af-actionbar-inner">
            <span className="af-unsaved" role="status"><i aria-hidden="true" />Unsaved changes</span>
            {missing.length > 0 ? (
              <button type="button" className="af-actionbar-missing" onClick={() => fillIn(missing[0])}>
                <AlertTriangle aria-hidden="true" />
                <span><b>Still missing {missing.length}:</b> {missing.slice(0, 3).map(missingLabel).join(', ')}{missing.length > 3 ? ` and ${missing.length - 3} more` : ''}</span>
              </button>
            ) : <span className="af-actionbar-ok">Everything required is filled in.</span>}
            <div className="af-actionbar-buttons">
              <Button variant="secondary" disabled={saving} onClick={() => { if (!window.confirm('Discard your changes to this form?')) return; state.discard() }} icon={<X />}>Discard</Button>
              <Button variant="primary" disabled={saving} onClick={() => void state.save()} icon={saving ? <Loader2 className="animate-spin" /> : <Save />}>{saving ? 'Saving…' : 'Save changes'}</Button>
            </div>
          </div>
        </div>
      )}

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
      {unmapOpen && application && (
        <UnmapCandidateDialog
          name={name}
          saving={unmapSaving}
          error={unmapError}
          onClose={() => setUnmapOpen(false)}
          onConfirm={async () => {
            if (unmapSaving) return
            setUnmapSaving(true); setUnmapError('')
            try {
              await store.unmap(application.id)
              setUnmapOpen(false)
              void state.reload(true)
              navigate(applicationFormHref(id, null, null), { replace: true })
            } catch (cause) { setUnmapError(cause instanceof Error ? cause.message : 'We could not remove the candidate from the job. Please try again.') }
            finally { setUnmapSaving(false) }
          }}
        />
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
    </div>
  )

  return createPortal(body, document.body)
}
