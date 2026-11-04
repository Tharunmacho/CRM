import { useCallback, useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Loader2, Pencil, Printer, RefreshCw, Save, X } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { useStore } from '@/shared/state/crm-store'
import { useLiveStatus } from '@/shared/state/live'
import { Button } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { useApplicationForm } from '@/features/application-form/use-application-form'
import { applicationFormHref } from '@/features/application-form/form-model'
import type { DocumentKind, MissingItem, TrackingRow } from '@/features/application-form/types'
import { FormErrorCard, FormSkeleton } from '@/features/application-form/components/form-states'
import { FormSheet } from '@/features/application-form/components/form-sheet'
import { SidePanel } from '@/features/application-form/components/side-panel'
import type { FormContext } from '@/features/application-form/components/sections'
import '@/styles/application-form.css'

/**
 * The application form, full screen and print-ready: /candidates/:id/application?order=:orderId.
 * It sits over the app shell (a portal), so the address is linkable and Print / Save as PDF prints only the form.
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
  const state = useApplicationForm(id, { orderId, applicationId })
  const { form, draft, setDraft, editing, setEditing, errors, dirty, saving, saveError, justUpdated } = state

  const [leaving, setLeaving] = useState(false)
  const [openStage, setOpenStage] = useState<{ stage: number } | null>(null)
  const [busyKinds, setBusyKinds] = useState<DocumentKind[]>([])

  const canWork = form ? form.candidate.canEdit : false
  const application = form?.application ? store.applications.find((item) => item.id === form.application?.id) : undefined
  const name = form?.personal.candidateName || form?.candidate.name || candidate?.name || 'Candidate'

  // The overlay covers the shell: lock its scroll and give the print stylesheet a hook to hide it.
  useEffect(() => {
    document.body.classList.add('af-open')
    const title = document.title
    return () => { document.body.classList.remove('af-open'); document.title = title }
  }, [])
  useEffect(() => { document.title = `Application form - ${name}` }, [name])

  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const close = useCallback(() => {
    if (editing && dirty) { setLeaving(true); return }
    if (window.history.length > 1) navigate(-1)
    else navigate(`/candidates/${id}`, { replace: true })
  }, [editing, dirty, navigate, id])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return
      if (document.querySelector('[role="dialog"][aria-modal="true"], .date-picker-popup, .custom-select-list')) return
      close()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [close])

  const ctx: FormContext | null = useMemo(() => (form && draft ? {
    form,
    draft,
    editing,
    errors,
    updated: justUpdated,
    setDraft: (update) => setDraft((current) => (current ? update(current) : current)),
  } : null), [form, draft, editing, errors, justUpdated, setDraft])

  const upload = useCallback(async (kind: DocumentKind, file: File) => {
    setBusyKinds((current) => [...current, kind])
    try {
      await state.upload(kind, file)
      store.notify(`${kind === 'photo' ? 'Photo' : kind === 'passport' ? 'Passport' : 'Resume'} uploaded${kind === 'photo' ? '' : '. Reading it now'}`)
    } finally {
      setBusyKinds((current) => current.filter((item) => item !== kind))
    }
  }, [state, store])

  const jump = useCallback((section: number) => {
    document.getElementById(`af-section-${section}`)?.scrollIntoView({ behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' })
  }, [])

  const uploadNow = useCallback((kind: DocumentKind) => {
    jump(5)
    window.setTimeout(() => document.querySelector<HTMLElement>(`[data-document="${kind}"] .ui-file-row-button`)?.focus(), 350)
  }, [jump])

  const fillIn = useCallback((item: MissingItem) => {
    setEditing(true)
    window.setTimeout(() => {
      const cell = document.querySelector<HTMLElement>(`[data-field="${item.key}"]`) ?? document.getElementById(`af-section-${item.section}`)
      cell?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      cell?.querySelector<HTMLElement>('input, textarea, button.custom-select-trigger, button.date-picker-trigger')?.focus({ preventScroll: true })
    }, 120)
  }, [setEditing])

  const openTracking = useCallback((row: TrackingRow) => setOpenStage({ stage: row.stage }), [])

  const completeness = state.completeness ?? form?.completeness ?? { percent: 0, filled: 0, total: 0, missing: [] }
  const missing = completeness.missing
  const hasOrder = Boolean(form?.application)

  const body = (
    <div className="af" role="dialog" aria-label={`Application form for ${name}`}>
      <div className="af-bar">
        <button type="button" className="af-bar-back" onClick={close}><ArrowLeft aria-hidden="true" /><span>Back</span></button>
        <div className="af-bar-title">
          <b>Application form</b>
          <span title={name}>{name}</span>
        </div>
        <span className={cn('af-live', liveStatus === 'up' && 'is-up')} title={liveStatus === 'up' ? 'Updates appear here as they happen' : 'Live updates are reconnecting; this page refreshes every few seconds'}>
          <i aria-hidden="true" />{state.reading ? 'Reading documents…' : liveStatus === 'up' ? 'Live' : 'Offline'}
        </span>
        <div className="af-bar-actions">
          {editing ? (
            <>
              <Button variant="ghost" disabled={saving} onClick={() => { if (dirty && !window.confirm('Discard your changes to this form?')) return; state.discard() }} icon={<X />}>Cancel</Button>
              <Button variant="primary" disabled={saving || !dirty} onClick={() => void state.save()} icon={saving ? <Loader2 className="animate-spin" /> : <Save />}>{saving ? 'Saving…' : 'Save changes'}</Button>
            </>
          ) : (
            <>
              {canWork && form && <Button variant="primary" onClick={() => setEditing(true)} icon={<Pencil />}>Edit form</Button>}
              <Button onClick={() => window.print()} icon={<Printer />} disabled={!form}>Print / PDF</Button>
            </>
          )}
        </div>
      </div>

      <div className="af-scroll">
        {state.loading && !form && !state.error ? (
          <FormSkeleton />
        ) : state.error && !form ? (
          <FormErrorCard error={state.error} onRetry={() => void state.reload(false)} onBack={close} />
        ) : form && ctx ? (
          <div className="af-layout">
            <SidePanel percent={completeness.percent} missing={missing} canEdit={canWork} onUpload={uploadNow} onFill={fillIn} onJump={jump} showExtras={form.extrasSupported} />
            <div className="af-main">
              {saveError && <p className="af-banner is-error" role="alert">{saveError}</p>}
              {form.warnings.length > 0 && <ul className="af-banner is-warn" role="status">{form.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}
              {form.applications.length > 1 && (
                <label className="af-switch">
                  <span>Order</span>
                  <select value={form.application?.id ?? ''} onChange={(event) => navigate(applicationFormHref(id, null, event.target.value), { replace: true })}>
                    {form.applications.map((item) => <option key={item.id} value={item.id}>{[item.orderRef, item.clientName, item.position].filter(Boolean).join(' · ')}</option>)}
                  </select>
                </label>
              )}
              {editing && <p className="af-banner" role="status">Editing. Fields marked Missing are empty. Changes are saved when you press Save changes.</p>}
              <FormSheet ctx={ctx} missing={missing} canUpload={canWork} upload={upload} busyKinds={busyKinds} canOpenStage={canWork && Boolean(application)} onOpenStage={openTracking} />
            </div>
          </div>
        ) : null}
      </div>

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
