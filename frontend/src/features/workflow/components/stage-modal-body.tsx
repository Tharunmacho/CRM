import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  BookUser,
  Briefcase,
  Building2,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleDot,
  CircleSlash,
  FileText,
  ListChecks,
  Lock,
  MessageSquarePlus,
  Phone,
  PhoneCall,
  RotateCcw,
  ShieldCheck,
  Trophy,
  User,
} from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { ApplicationFormButton } from '@/features/application-form/components/application-form-button'
import { currencyChoices, currencyLabel } from '@/shared/lib/agency'
import { dateLabel, dateTimeLabel, money, smartTime } from '@/shared/lib/format'
import { applicationFileSlot, applicationFileVersions, newestFileVersions, type ApplicationFile } from '@/shared/lib/document-files'
import { useStore } from '@/shared/state/crm-store'
import { DatePicker } from '@/shared/ui/date-picker'
import { ApiError, api } from '@/shared/api/client'
import { taskClockStart } from '@/shared/lib/task-time'
import {
  STAGES,
  closureLabels,
  fieldVisible,
  stageByNo,
  userRefId,
  userRefName,
  type StageField,
  type UserRef,
} from '@/shared/workflow/stages'
import type { Application, FileVersion, StageRecord } from '@/shared/model/types'
import { Dialog } from '@/shared/ui/overlay'
import { Badge } from '@/shared/ui/display'
import {
  Button,
  Checkbox,
  Field,
  Input,
  RadioRow,
  Select,
  Textarea,
} from '@/shared/ui/primitives'
import { FileDrop, type StoredFile } from '@/shared/ui/file-drop'
import { useWorkspaceReady } from '@/features/master-data/use-workspace-ready'
import { PersonAvatar } from './person-avatar'
import { DocumentList } from '@/features/candidates/components/profile-documents'
import { SlaMeter, StageRail } from './stage-rail'
import { canWorkCandidate, workRefusal } from '@/features/candidates/candidate-access'
import { shortRef } from '@/features/candidates/components/data-bank-columns'
import '@/styles/candidate-v2.css'
import '@/styles/stage-modal.css'

/**
 * One scrolling application: completed sections keep their saved feedback,
 * the current stage is editable, and all later stages remain visible for reference.
 */
export function StageModal({
  applicationId,
  open,
  onClose,
  focusStage,
  embedded = false,
  onDirtyChange: reportDirty,
  onBusyChange: reportBusy,
  onStageChanged,
}: {
  applicationId: string | null
  open: boolean
  onClose: () => void
  /** Open this stage's section (expanded) and scroll to it, e.g. when a task for that stage was clicked. */
  focusStage?: number
  /** Show only the selected stage inside the candidate's recruitment card. */
  embedded?: boolean
  onDirtyChange?: (dirty: boolean) => void
  onBusyChange?: (busy: boolean) => void
  onStageChanged?: () => void
}) {
  const store = useStore()
  const snapshot = useWorkspaceReady(open && applicationId ? `stage:${applicationId}` : null)
  useEffect(() => {
    if (embedded || !open || !focusStage || !snapshot.ready) return
    const timer = window.setTimeout(() => {
      const section = document.querySelector<HTMLElement>(`[data-sm-stage="${focusStage}"]`)
      if (!section) return
      section.querySelector('details')?.setAttribute('open', '')
      section.scrollIntoView({ block: 'start', behavior: 'smooth' })
    }, 80)
    return () => window.clearTimeout(timer)
  }, [open, focusStage, snapshot.ready, applicationId, embedded])
  // Open on the stage that needs work: finished stages stay collapsed above, so the current one is the first thing you edit.
  useEffect(() => {
    if (embedded || !open || !snapshot.ready || focusStage) return
    const timer = window.setTimeout(() => {
      const steps = document.querySelectorAll<HTMLElement>('.sm-timeline > .sm-step')
      const target = document.querySelector<HTMLElement>('.sm-timeline > .sm-step.is-current') ?? steps[steps.length - 1]
      target?.scrollIntoView({ block: 'start' })
    }, 120)
    return () => window.clearTimeout(timer)
  }, [open, snapshot.ready, applicationId, embedded])
  const app = store.applications.find((a) => a.id === applicationId)
  // Who reopened an earlier try, and when. A failed read is not an error: the reason is still shown.
  const [reopenings, setReopenings] = useState<Reopening[]>([])
  const reopenCount = app ? app.stages.filter((record) => record.reopenedReason).length : 0
  useEffect(() => {
    if (!open || !applicationId || reopenCount === 0) { setReopenings([]); return }
    let current = true
    api.get<Reopening[]>(`/applications/${encodeURIComponent(applicationId)}/reopenings`)
      .then((rows) => { if (current && Array.isArray(rows)) setReopenings(rows) })
      .catch(() => { if (current) setReopenings([]) })
    return () => { current = false }
  }, [open, applicationId, reopenCount])
  const [files, setFiles] = useState<ApplicationFile[]>([])
  const [fileError, setFileError] = useState(false)
  const [fileRetry, setFileRetry] = useState(0)
  const [dirty, setDirty] = useState(false)
  const [pending, setPending] = useState(false)
  const [reopening, setReopening] = useState(false)

  useEffect(() => { reportDirty?.(dirty); return () => reportDirty?.(false) }, [dirty, reportDirty])
  useEffect(() => { reportBusy?.(pending || reopening); return () => reportBusy?.(false) }, [pending, reopening, reportBusy])

  useEffect(() => {
    if (!open || !applicationId) return
    let live = true
    setFiles([])
    setFileError(false)
    // The files of a stage are the assigned recruiter's to open (the server refuses anyone else); nothing to fetch for a read-only visitor.
    if (!canWorkCandidate(store.me, app ? store.candidate(app.candidateId) : undefined)) return
    api.get<ApplicationFile[]>(`/files?entity_type=application&entity_id=${encodeURIComponent(applicationId)}`)
      .then((rows) => { if (live) setFiles((current) => [...rows, ...current.filter((file) => !rows.some((row) => row.id === file.id))]) })
      .catch(() => { if (live) setFileError(true) })
    return () => { live = false }
  }, [applicationId, open, fileRetry])

  const cand = app ? store.candidate(app.candidateId) : undefined
  const order = app ? store.order(app.orderId) : undefined
  const client = order ? store.company(order.companyId) : undefined
  const designation = order ? store.designation(order.designationId) : undefined
  if (!snapshot.ready || !app || !cand || !order || !client || !designation) {
    const state = <div className="sm sm-state"><p role={snapshot.error ? 'alert' : 'status'} className="text-sm text-ink-600">{snapshot.error || (!snapshot.ready ? 'Loading the stages…' : 'We could not find this order for the candidate any more. Refresh and try again.')}</p>{(snapshot.error || snapshot.ready) && <Button className="mt-4" variant="primary" onClick={snapshot.retry}>Refresh</Button>}</div>
    return embedded ? state : <Dialog open={open} onClose={onClose} title={cand?.name ?? 'Stages for this order'} description={cand ? 'Stages for this order' : undefined}>{state}</Dialog>
  }
  const owner = store.user(cand.ownerId)

  const canWork = canWorkCandidate(store.me, cand)
  const readOnlyReason = workRefusal(store.me, cand, owner?.name)
  const effective = app.stages.filter((record) => !record.reopenedReason)
  const superseded = app.stages.filter((record) => record.reopenedReason)
  const live = app.status === 'active' ? app.currentStage : null
  const doneStages = STAGES.filter((definition) => effective.some((record) => record.stage === definition.no) && definition.no !== live)
  const recorded = new Set(effective.map((record) => record.stage)).size

  if (embedded) {
    if (!open) return null
    const selectedStage = stageByNo(focusStage ?? app.currentStage) ?? stageByNo(app.currentStage)
    const selectedRecords = app.stages.filter((record) => record.stage === selectedStage.no).sort((a, b) => b.attempt - a.attempt)
    const activeRecords = selectedRecords.filter((record) => !record.reopenedReason)
    const current = live === selectedStage.no
    return <div className="sm sm-embedded" data-sm-stage={selectedStage.no}>
      {!canWork && <p className="sm-note"><Lock className="h-4 w-4" aria-hidden="true" />{readOnlyReason}</p>}
      {!owner?.active && <p className="sm-note">Assign an active recruiter before moving or reopening a stage. You can still save a draft.</p>}
      {fileError && <p className="sm-alert" role="alert">We could not load the documents. Feedback is still available. <button type="button" className="underline" onClick={() => setFileRetry((value) => value + 1)}>Retry documents</button></p>}
      {current ? <CurrentStage key={`${app.id}-${app.currentStage}-${app.stages.length}`} app={app} canWork={canWork && !reopening} onDirtyChange={setDirty} onBusyChange={setPending} files={files} onUploaded={(file) => setFiles((items) => [...items.filter((item) => item.id !== file.id), file])} onStageChanged={onStageChanged} compact />
        : <section className="sm-card sm-inline-history">
          <header className="sm-inline-title"><div><small>Stage {selectedStage.no} of {STAGES.length}</small><h3>{selectedStage.title}</h3></div><Badge tone={activeRecords.length ? 'green' : 'neutral'} variant="dot">{activeRecords.length ? 'Saved history' : 'Not started'}</Badge></header>
          {selectedRecords.length === 0 && <p className="sm-hint">No feedback has been saved for this stage yet. {app.status === 'active' ? `Complete stage ${app.currentStage} to continue in order.` : 'Reopen the application to continue working on its stages.'}</p>}
          {selectedRecords.map((record) => <div className="sm-inline-attempt" key={`${record.stage}-${record.attempt}`}>{record.reopenedReason && <ReopenedNote record={record} reopenings={reopenings} />}<CompletedStage record={record} files={files} app={app} /></div>)}
          {activeRecords.length > 0 && <ReopenStage app={app} stage={selectedStage.no} disabled={pending || reopening || !owner?.active} onBusyChange={setReopening} beforeReopen={() => !dirty || window.confirm('Discard unsaved feedback and reopen this stage?')} onStageChanged={onStageChanged} />}
        </section>}
      {current && selectedRecords.length > 0 && <details className="sm-extra"><summary>Earlier feedback and attempts ({selectedRecords.length})</summary><div className="mt-4 space-y-6">{selectedRecords.map((record) => <div key={`${record.stage}-${record.attempt}`}>{record.reopenedReason && <ReopenedNote record={record} reopenings={reopenings} />}<CompletedStage record={record} files={files} app={app} /></div>)}</div></details>}
    </div>
  }

  return (
    <Dialog
      open={open}
      onClose={() => { if (!pending && !reopening && (!dirty || window.confirm('Throw away changes you have not saved?'))) onClose() }}
      width="full"
      title="Stages for this order"
      description={`${cand.name} · ${order.ref}`}
    >
      <div className="sm">
        <div className="sm-wrap">
          <ApplicationHeader app={app} recorded={recorded} effective={effective} />

          {!owner?.active && <p role="status" className="sm-note"><AlertTriangle className="h-4 w-4" aria-hidden="true" />Give this candidate an active recruiter (on their profile) before you finish or reopen a stage. You can still save drafts.</p>}
          {!canWork && (
            <p className="sm-note">
              <Lock className="h-4 w-4" aria-hidden="true" />
              {readOnlyReason} You can read the stages, but you cannot fill them in.
            </p>
          )}

          <ol className="sm-timeline" aria-label="All recruitment stages">
            {fileError && <li className="sm-alert" role="alert">We could not load the files. <button type="button" className="underline" onClick={() => setFileRetry((v) => v + 1)}>Try again</button></li>}
            {doneStages.map((definition) => {
              const records = effective.filter((record) => record.stage === definition.no)
              const latest = records[records.length - 1]
              const negative = NEGATIVE_OUTCOMES.includes(latest.outcome)
              return <li key={`${definition.no}-${latest.attempt}`} data-sm-stage={definition.no} className={cn('sm-step', negative ? 'is-closed' : 'is-done')}>
                <span className="sm-node" aria-hidden="true">{negative ? definition.no : <Check className="h-4 w-4" />}</span>
                <details className="sm-card group">
                  <summary className="sm-summary">
                    <span className="sm-summary-copy"><b>{definition.no}. {definition.title}</b><span>Completed by {store.user(latest.completedBy)?.name ?? 'Former team member'} · {dateTimeLabel(latest.completedAt)}</span></span>
                    <Badge tone={negative ? 'stamp' : 'green'} variant="dot">{latest.outcome}</Badge>
                    <ChevronDown className="sm-chev h-4 w-4" aria-hidden="true" />
                  </summary>
                  <div className="sm-done-body">{records.map((record) => <CompletedStage key={`${record.stage}-${record.attempt}`} record={record} files={files} app={app} />)}
                    <ReopenStage app={app} stage={definition.no} disabled={pending || reopening || !owner?.active} onBusyChange={setReopening} beforeReopen={() => !dirty || window.confirm('Reopening replaces the stage you are on and throws away changes you have not saved. Continue?')} onStageChanged={onStageChanged} />
                  </div>
                </details>
              </li>
            })}
            {live === 6 && effective.some((record) => record.stage === 6) && <li className="sm-step is-done">
              <span className="sm-node" aria-hidden="true">6</span>
              <details className="sm-card">
                <summary className="sm-summary">
                  <span className="sm-summary-copy"><b>Previous medical attempts ({effective.filter((record) => record.stage === 6).length})</b><span>The medical is being done again. Earlier tries stay on record.</span></span>
                  <Badge tone="amber" variant="dot">Unfit: reapplying</Badge>
                  <ChevronDown className="sm-chev h-4 w-4" aria-hidden="true" />
                </summary>
                <div className="sm-done-body">{effective.filter((record) => record.stage === 6).map((record) => <CompletedStage key={record.attempt} record={record} files={files} app={app} />)}</div>
              </details>
            </li>}
            {app.status === 'active' ? (
              <li className="sm-step is-current" data-sm-stage={app.currentStage}>
                <span className="sm-node" aria-hidden="true">{app.currentStage}</span>
                <CurrentStage
                  key={`${app.id}-${app.currentStage}-${app.stages.length}`}
                  app={app}
                  canWork={canWork && !reopening}
                  onDirtyChange={setDirty}
                  onBusyChange={setPending}
                  files={files}
                  onUploaded={(file) => setFiles((current) => [...current.filter((item) => item.id !== file.id), file])}
                  onStageChanged={onStageChanged}
                />
              </li>
            ) : app.status === 'deployed' ? (
              <li className="sm-step is-deployed"><span className="sm-node" aria-hidden="true"><Trophy className="h-4 w-4" /></span><DeployedRow /></li>
            ) : (
              <li className="sm-step is-closed"><span className="sm-node" aria-hidden="true"><CircleSlash className="h-4 w-4" /></span><ClosedRow app={app} /></li>
            )}
            {STAGES.filter((definition) => definition.no > app.currentStage && !effective.some((record) => record.stage === definition.no)).map((definition) => (
              <li key={definition.no} className="sm-step is-upcoming" data-sm-stage={definition.no}>
                <span className="sm-node" aria-hidden="true">{definition.no}</span>
                <details className="sm-card">
                  <summary className="sm-summary">
                    <span className="sm-summary-copy"><b>{definition.no}. {definition.title}</b><span>Not started</span></span>
                    <ChevronDown className="sm-chev h-4 w-4" aria-hidden="true" />
                  </summary>
                  <div className="sm-done-body"><p className="sm-hint">{definition.summary}</p><p className="sm-hint">No feedback has been saved for this stage. {app.status === 'active' ? 'Save feedback and complete the current stage to continue in order.' : 'The application must be reopened before work can continue.'}</p></div>
                </details>
              </li>
            ))}
          </ol>

          {superseded.length > 0 && <details className="sm-extra">
            <summary>Earlier tries kept after reopening ({superseded.length})</summary>
            <p className="sm-hint">These earlier tries stay on record. They do not count towards the progress now.</p>
            <div className="mt-4 space-y-6">{superseded.map((record) => <div key={`${record.stage}-${record.attempt}`}>
              <ReopenedNote record={record} reopenings={reopenings} />

              <CompletedStage record={record} files={files} app={app} />
            </div>)}</div>
          </details>}
        </div>
      </div>
    </Dialog>
  )
}

const NEGATIVE_OUTCOMES = ['Failed', 'Rejected', 'Unfit', 'Not forwarded']

/**
 * How many of the form's 12 columns each field takes. Short controls take 4 (three to a row), a longer
 * choice takes 8, a plain text field fills what is left of its row (all of it when the row is empty),
 * and files, notes and checkboxes always take the full row.
 */
function fieldSpans(fields: StageField[]): number[] {
  let used = 0
  return fields.map((field) => {
    const choiceLength = (field.options ?? []).reduce((total, option) => total + option.label.length, 0)
    let span: number
    if (field.type === 'file' || field.type === 'textarea' || field.type === 'checkbox') span = 12
    else if (field.type === 'radio') span = choiceLength <= 20 ? 4 : choiceLength <= 36 ? 8 : 12
    else if (field.type === 'text' && field.width !== 'half') span = used > 0 && 12 - used >= 8 ? 12 - used : 12
    else span = 4
    if (used + span > 12) used = 0
    used += span
    if (used >= 12) used = 0
    return span
  })
}

/** One row of GET /applications/{id}/reopenings: who set aside which attempt of a stage, and when. */
interface Reopening { stage: number; attempt: number; reopenedBy?: string | null; reopenedByName?: string | null; reopenedAt?: string | null; reason?: string | null }

/** "Reopened by Priya on 12 Mar 2026, 10:30: reason". Who and when only show when the server returned them. */
function ReopenedNote({ record, reopenings }: { record: StageRecord; reopenings: Reopening[] }) {
  const store = useStore()
  const found = reopenings.find((item) => item.stage === record.stage && item.attempt === record.attempt)
  const who = found ? found.reopenedByName || (found.reopenedBy ? store.user(found.reopenedBy)?.name : '') || '' : ''
  const when = found?.reopenedAt ? dateTimeLabel(found.reopenedAt) : ''
  const lead = who || when ? `Reopened${who ? ` by ${who}` : ''}${when ? ` on ${when}` : ''}` : 'Reopened'
  return <p className="sm-reopened-note">{lead}: {record.reopenedReason || found?.reason}</p>
}

/**
 * Header: candidate name, photo, phone, passport number, client, order, designation, owner,
 * current stage and overall status, with a numbered progress strip for the stages reached.
 * The full candidate record opens below it on request.
 */
function ApplicationHeader({ app, recorded, effective }: { app: Application; recorded: number; effective: StageRecord[] }) {
  const store = useStore()
  const [record, setRecord] = useState(false)
  const cand = store.candidate(app.candidateId)!
  const order = store.order(app.orderId)!
  const client = store.company(order.companyId)!
  const designation = store.designation(order.designationId)!
  const owner = store.user(cand.ownerId)
  const photo = newestFileVersions(cand.documents.find(document => document.key === 'photo')?.versions ?? [])[0]
  // The order and the stage are already in the title line and the progress strip, so they are not repeated here.
  const facts: [string, React.ReactNode, React.ReactNode, boolean?][] = [
    ['Phone', <Phone key="i" className="h-3.5 w-3.5" aria-hidden="true" />, cand.phone, true],
    ['Passport', <BookUser key="i" className="h-3.5 w-3.5" aria-hidden="true" />, cand.passportNo ?? 'Not saved', !!cand.passportNo],
    ['Passport expires', <BookUser key="i" className="h-3.5 w-3.5" aria-hidden="true" />, cand.passportExpiry ? dateLabel(cand.passportExpiry) : 'Not saved'],
    ['Recruiter', <User key="i" className="h-3.5 w-3.5" aria-hidden="true" />, owner?.name ?? 'Not assigned'],
    ['Client', <Building2 key="i" className="h-3.5 w-3.5" aria-hidden="true" />, client.name],
    ['Job title', <Briefcase key="i" className="h-3.5 w-3.5" aria-hidden="true" />, designation.title],
    ['Matched on', <FileText key="i" className="h-3.5 w-3.5" aria-hidden="true" />, dateLabel(app.createdAt)],
    ['Last update', <ListChecks key="i" className="h-3.5 w-3.5" aria-hidden="true" />, smartTime(app.updatedAt)],
  ]
  const statusTone = app.status === 'closed' ? 'stamp' : app.status === 'deployed' ? 'green' : 'navy'
  const statusLabel = app.status === 'active' ? 'Active' : app.status === 'deployed' ? 'Deployed' : 'Closed'
  const progressLead = app.status === 'active' ? `Stage ${app.currentStage} of ${STAGES.length} · ${stageByNo(app.currentStage).title}` : app.status === 'deployed' ? 'Deployed' : `Closed at stage ${app.currentStage}${app.closureReason ? ` · ${closureLabels[app.closureReason]}` : ''}`
  const progressText = app.status === 'active' ? 'Write feedback and choose Save and move to advance manually. Documents are optional for stage movement.' : app.status === 'deployed' ? 'All 11 stages are done. Open any stage to read its saved feedback.' : 'This order is closed for this candidate. Saved feedback, results and documents are below.'
  return (
    <>
      <header className="sm-head">
        <div className="sm-head-main">
          <PersonAvatar name={cand.name} photoUrl={photo?.url} size={56} />
          <div className="sm-id">
            <h3 className="sm-name" title={cand.name}>{cand.name}</h3>
            <p className="sm-sub"><span>Order</span><b className="tnum" title={order.ref}>{order.ref}</b><i aria-hidden="true">·</i><span>Candidate</span><b className="tnum" title={cand.ref}>{shortRef(cand.ref)}</b></p>
          </div>
          <div className="sm-head-side">
            <Badge tone={statusTone} variant="dot">{statusLabel}</Badge>
          </div>
        </div>
        <dl className="sm-facts">
          {facts.map(([label, icon, value, ident]) => <div key={label}><dt>{icon}{label}</dt><dd className={cn(ident && 'ident')} title={typeof value === 'string' ? value : undefined}>{value}</dd></div>)}
        </dl>
        <div className="sm-progress">
          <div className="sm-progress-line">
            <p className="sm-progress-label"><b>{progressLead}</b><span>{progressText}</span></p>
            <Badge className="tnum">{recorded} of {STAGES.length} stages done</Badge>
          </div>
          <StageRail status={app.status} currentStage={app.currentStage} reached={[...new Set(effective.map(item => item.stage))]} numbered />
        </div>
        <div className="sm-head-foot">
          <ApplicationFormButton size="sm" variant="primary" candidateId={cand.id} orderId={order.id} />
          <button type="button" className="sm-record-toggle" aria-expanded={record} onClick={() => setRecord((value) => !value)}>
            <User className="h-4 w-4" aria-hidden="true" />{record ? 'Hide full candidate details' : 'See full candidate details'}<ChevronDown className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </header>
      {record && <CandidateRecord app={app} />}
    </>
  )
}

/**
 * The full candidate record, in segments. Collapsed by default so the stage work
 * stays the first thing you see; opening it reveals everything on file.
 */
function CandidateRecord({ app }: { app: Application }) {
  const store = useStore()
  const cand = store.candidate(app.candidateId)!
  const order = store.order(app.orderId)!
  const client = store.company(order.companyId)!
  const designation = store.designation(order.designationId)!

  return (
    <div className="sm-record">
      <Segment icon={<User className="h-4 w-4" />} title="Personal information" subtitle="Basic details">
        <Fact label="Name" value={cand.name} />
        <Fact label="Reference" value={cand.ref} ident />
        <Fact label="Date of birth" value={dateLabel(cand.dob)} />
        <Fact label="Gender" value={cand.gender ?? 'Not provided'} className="capitalize" />
        <Fact label="Nationality" value={cand.nationality || 'Not provided'} />
        <Fact label="Current location" value={cand.currentCity || 'Not provided'} />
        <Fact label="Languages known" value={cand.languages.join(', ') || 'Not provided'} half />
      </Segment>

      <Segment icon={<Phone className="h-4 w-4" />} title="Contact and source" subtitle="How to reach this candidate and where they came from">
        <Fact label="Mobile" value={cand.phone} ident />
        <Fact label="WhatsApp" value={cand.whatsapp ?? 'Not provided'} ident={!!cand.whatsapp} />
        <Fact label="Email" value={cand.email ?? 'Not provided'} half />
        <Fact label="Source" value={cand.source.replace('_', '-') + (cand.sourceDetail ? ` · ${cand.sourceDetail}` : '')} className="capitalize" half />
        <Fact label="Date added" value={dateLabel(cand.addedAt)} />
      </Segment>

      <Segment icon={<BookUser className="h-4 w-4" />} title="Passport" subtitle="Passport details we have saved. The original passport is collected at stage 7.">
        <Fact label="Passport number" value={cand.passportNo ?? 'Not on file'} ident={!!cand.passportNo} />
        <Fact label="Place of issue" value={cand.passportPlace ?? 'Not provided'} />
        <Fact label="Issue date" value={dateLabel(cand.passportIssue)} />
        <Fact label="Expiry date" value={dateLabel(cand.passportExpiry)} />
      </Segment>

      <Segment icon={<Briefcase className="h-4 w-4" />} title="Work profile" subtitle="Trade, experience and expectations">
        <Fact label="Preferred job title" value={cand.designations.join(', ') || 'Not provided'} half />
        <Fact label="Total experience" value={cand.totalExperience == null ? 'Not recorded' : `${cand.totalExperience} years`} />
        <Fact label="Gulf or overseas experience" value={cand.gulfExperience == null ? 'Not recorded' : `${cand.gulfExperience} years`} />
        <Fact label="Expected salary" value={money(cand.expectedSalary)} />
        <div className="sm-fact is-wide">
          <dt>Skills</dt>
          <dd className="mt-1.5 flex flex-wrap gap-1.5">
            {cand.skills.length ? cand.skills.map((sk) => <Badge key={sk}>{sk}</Badge>) : <span className="text-ink-400">Not provided</span>}
          </dd>
        </div>
      </Segment>

      <Segment icon={<Building2 className="h-4 w-4" />} title="Order" subtitle="The order this candidate is matched to">
        <Fact label="Employer" value={client.name} />
        <Fact label="Client contact" value={store.client(client.clientId)?.name ?? 'Not available'} />
        <Fact label="Job title" value={designation.title} />
        <Fact label="Order number" value={order.ref} ident />
        <Fact label="Location" value={[order.city, order.country].filter(Boolean).join(', ') || 'Not recorded'} />
        <Fact label="Salary" value={money(order.salary, order.currency)} />
        <Fact label="Contract period" value={order.contractPeriod || 'Not recorded'} />
        <Fact label="Interview date" value={dateLabel(order.interviewDate)} />
        <Fact label="Order deadline" value={dateLabel(order.deadline)} />
      </Segment>

      {cand.callOutcome && (
        <Segment icon={<PhoneCall className="h-4 w-4" />} title="Initial call" subtitle="Result of the first call">
          <Fact label="Interested" value={cand.callOutcome.interested === 'yes' ? 'Yes' : cand.callOutcome.interested === 'no' ? 'No' : 'Not reachable'} />
          <Fact label="Recorded" value={dateTimeLabel(cand.callOutcome.recordedAt)} />
          <Fact label="Remarks" value={cand.callOutcome.remarks || 'Not provided'} half />
        </Segment>
      )}

      <Segment icon={<FileText className="h-4 w-4" />} title="Documents" subtitle="Nothing is overwritten. A new file is saved as a new version.">
        <div className="sm-fact is-wide">
          <DocumentList documents={cand.documents} uploaderName={(id) => store.user(id)?.name || 'Former team member'} />
        </div>
      </Segment>
    </div>
  )
}

function Segment({ icon, title, subtitle, children }: { icon: React.ReactNode; title: string; subtitle: string; children: React.ReactNode }) {
  return (
    <section className="sm-seg">
      <header>
        <span className="sm-seg-icon">{icon}</span>
        <div className="min-w-0"><h4>{title}</h4><p>{subtitle}</p></div>
      </header>
      <dl className="sm-seg-body">{children}</dl>
    </section>
  )
}

/** A read-only field, boxed so the record scans like a filled-in form. */
function Fact({ label, value, ident, className, half }: { label: string; value: React.ReactNode; ident?: boolean; className?: string; half?: boolean }) {
  return (
    <div className={cn('sm-fact', half && 'is-half')}>
      <dt>{label}</dt>
      <dd className={cn(ident && 'ident', className)}>{value}</dd>
    </div>
  )
}

/* -------------------------------------------------------- Completed stage */

function CompletedStage({ record, files, app }: { record: StageRecord; files: ApplicationFile[]; app: Application }) {
  const store = useStore()
  const def = stageByNo(record.stage)
  const by = store.user(record.completedBy)
  const negative = NEGATIVE_OUTCOMES.includes(record.outcome)

  return (
    <section>
      <header className="sm-attempt-head">
        <div>
          <h3>Attempt {record.attempt}</h3>
          <p>Recorded by {by?.name ?? 'Former team member'} on {dateTimeLabel(record.completedAt)}</p>
        </div>
        <Badge tone={negative ? 'stamp' : 'green'} variant="dot">{record.outcome}</Badge>
      </header>

      <dl className="sm-record-fields">
        {def.fields
          .filter((f) => fieldVisible(f, record.data) && record.data[f.name] != null)
          .map((f) => (
            <div key={f.name} className={cn('sm-fact', (f.type === 'file' || f.type === 'textarea') && 'is-wide')}>
              <dt>{f.label}</dt>
              <dd className={cn(f.ident && 'ident', f.type === 'textarea' && 'whitespace-pre-line')}>
                {f.type === 'file' && (record.data[f.name] as StoredFile).url ? (
                  <FileDrop value={record.data[f.name] as StoredFile} versions={applicationFileVersions(files, app, record.stage, f.name)} disabled uploaderName={(id) => store.user(id)?.name || 'Former team member'} />
                ) : (
                  readValue(f, record.data[f.name], record.data)
                )}
              </dd>
            </div>
          ))}
      </dl>

      {!record.data.feedback && <p className="sm-hint">No stage feedback was recorded for this earlier attempt.</p>}

      <p className="sm-hint">Saved attempts cannot be changed. If an admin reopens the stage, these details and files stay on record.</p>
    </section>
  )
}

function ReopenStage({ app, stage, disabled, onBusyChange, beforeReopen, onStageChanged }: { app: Application; stage: number; disabled: boolean; onBusyChange: (busy: boolean) => void; beforeReopen: () => boolean; onStageChanged?: () => void }) {
  const store = useStore()
  const [editing, setEditing] = useState(false)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  useEffect(() => { if (busy) onBusyChange(true); return () => { if (busy) onBusyChange(false) } }, [busy, onBusyChange])
  if (!store.can('reopen_stage')) return null
  const reopen = async () => {
    if (!reason.trim() || busy || disabled || saved || !beforeReopen()) return
    setBusy(true); setError('')
    try {
      await api.post(`/applications/${app.id}/reopen`, { stage, reason: reason.trim(), lock_version: app.lockVersion })
      if (!store.isCurrentSession()) return
      setSaved(true)
      store.notify(`Stage ${stage} reopened. Earlier tries stay on record.`)
      try { await store.refresh({ keepCurrent: true }); onStageChanged?.() }
      catch { setError('Reopened. Refresh to see the current stage.') }
    } catch (cause) {
      if (store.isCurrentSession()) setError(cause instanceof Error ? cause.message : 'We could not reopen the stage. Please try again.')
    } finally { setBusy(false) }
  }
  return <div className="sm-reopen">
    {error && <p role="alert" className="mb-3 text-sm text-stamp-700">{error}</p>}
    {saved ? <Button size="sm" onClick={() => void store.refresh({ keepCurrent: true }).catch(() => setError('We could not refresh. Please try again.'))}>Refresh</Button>
      : editing ? <div className="space-y-3">
        <p className="text-[13px] leading-5 text-ink-600">Admin only. This starts stage {stage} again, sets aside the results of any later stages, and replaces the task that is open now. Earlier records and files stay on record. Reopening a deployment also changes the order's deployed count.</p>
        <Field label="Reason for reopening" required><Textarea value={reason} onChange={(event) => setReason(event.target.value)} maxLength={2000} disabled={busy || disabled} placeholder="What needs to be corrected?" /></Field>
        <div className="flex flex-wrap gap-2"><Button variant="primary" disabled={busy || disabled || !reason.trim()} onClick={() => void reopen()}>{busy ? 'Reopening…' : `Reopen stage ${stage}`}</Button><Button disabled={busy || disabled} onClick={() => { setEditing(false); setError('') }}>Cancel</Button></div>
      </div> : <Button variant="secondary" size="sm" disabled={disabled} icon={<RotateCcw className="h-4 w-4" />} onClick={() => setEditing(true)}>Reopen stage {stage}</Button>}
  </div>
}

function readValue(f: StageField, raw: unknown, row: Record<string, unknown> = {}): string {
  if (raw == null || raw === '') return '—'
  if (f.type === 'file') return (raw as StoredFile).name ?? 'Uploaded'
  if (f.type === 'checkbox') return raw ? 'Yes' : 'No'
  if (f.type === 'date') return dateLabel(raw as string)
  // The advance is shown in the currency it was collected in, not the order's.
  if (f.type === 'number')
    return f.name === 'amount'
      ? money(raw as number, (row.amountCurrency as string) || null)
      : String(raw)
  if (f.optionsSource === 'users') return userRefName(raw) || '—'
  const option = f.options?.find((o) => o.value === raw)
  return option?.label ?? String(raw)
}

/* ---------------------------------------------------------- Current stage */

function CurrentStage({
  app,
  canWork,
  onDirtyChange,
  onBusyChange,
  files,
  onUploaded,
  compact = false,
  onStageChanged,
}: {
  app: Application
  canWork: boolean
  onDirtyChange: (dirty: boolean) => void
  onBusyChange: (busy: boolean) => void
  files: ApplicationFile[]
  onUploaded: (file: ApplicationFile) => void
  compact?: boolean
  onStageChanged?: () => void
}) {
  const store = useStore()
  const { addRemark } = store
  const def = stageByNo(app.currentStage)
  const ownerActive = !!store.user(store.candidate(app.candidateId)?.ownerId ?? '')?.active

  // Edits are held locally until Save. Nothing is written as you type, so the form has
  // a dirty state and leaving with unsaved work is a deliberate act, not an accident.
  const candidate = store.candidate(app.candidateId)
  const profilePassport = newestFileVersions(candidate?.documents.find((document) => document.key === 'passport')?.versions ?? [])[0]
  const profileAirTicket = newestFileVersions(candidate?.documents.find((document) => document.key === 'air_ticket')?.versions ?? [])[0]
  const profilePassportId = profilePassport?.id
  const profileAirTicketId = profileAirTicket?.id
  // Stage 7 starts from the passport copy already on the profile; the recruiter may replace it.
  // Stage 11 likewise starts from the Air ticket already saved in the
  // candidate's Documents section. It is the same attachment, not a second
  // upload, and the server still requires a travel date before deployment.
  const withCandidateDocuments = (draft: Record<string, unknown>) => {
    let prepared = draft
    if (app.currentStage === 7 && !prepared.copy && profilePassport) {
      prepared = { ...prepared, copy: { id: profilePassport.id, name: profilePassport.name, sizeKb: profilePassport.sizeKb, url: profilePassport.url, version: profilePassport.version } }
    }
    if (app.currentStage === 11 && !prepared.file && profileAirTicket) {
      prepared = { ...prepared, file: { id: profileAirTicket.id, name: profileAirTicket.name, sizeKb: profileAirTicket.sizeKb, url: profileAirTicket.url, version: profileAirTicket.version } }
    }
    return prepared
  }
  const [data, setData] = useState<Record<string, unknown>>(() => withCandidateDocuments(app.draft ?? {}))
  const [lockVersion, setLockVersion] = useState(app.lockVersion ?? 0)
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [uploadingFields, setUploadingFields] = useState<string[]>([])
  const uploading = uploadingFields.length > 0
  const result = useMemo(() => def.evaluate(data), [def, data])
  const [attempted, setAttempted] = useState(false)
  const [error, setError] = useState('')
  const [conflict, setConflict] = useState<{ draft?: Record<string, unknown>; lock_version?: number; current_stage?: number; status?: string } | null>(null)
  const [completed, setCompleted] = useState(false)
  const [refreshRequired, setRefreshRequired] = useState(false)
  const [remarkBusy, setRemarkBusy] = useState(false)

  useEffect(() => { onDirtyChange(dirty); return () => onDirtyChange(false) }, [dirty, onDirtyChange])
  useEffect(() => {
    onBusyChange(busy || uploading || remarkBusy)
    return () => onBusyChange(false)
  }, [busy, uploading, remarkBusy, onBusyChange])

  useEffect(() => {
    if (!dirty) { setData(withCandidateDocuments(app.draft ?? {})); setLockVersion(app.lockVersion ?? 0) }
  }, [app.lockVersion, app.currentStage, profilePassportId, profileAirTicketId])

  const task = store.tasks.find(
    (t) => t.applicationId === app.id && t.stage === app.currentStage && !t.completedAt && !t.cancelledAt,
  )
  const remarks = task ? store.remarksFor(task.id) : []
  const [remark, setRemark] = useState('')

  const set = (name: string, value: unknown) => {
    setData((d) => ({ ...d, [name]: value }))
    setDirty(true)
  }

  const reportFailure = (cause: unknown) => {
    if (!store.isCurrentSession()) return
    const details = cause instanceof ApiError && cause.detail && typeof cause.detail === 'object'
      ? cause.detail as { missing?: string[]; draft?: Record<string, unknown>; lock_version?: number; current_stage?: number; status?: string } : null
    setError([cause instanceof Error ? cause.message : 'We could not save this. Please try again.', ...(details?.missing ?? [])].join(' '))
    if (cause instanceof ApiError && cause.isConflict) setConflict(details)
  }

  const refreshAfterWrite = async () => {
    try { await store.refresh({ keepCurrent: true }); setRefreshRequired(false); setError(''); onStageChanged?.() }
    catch { if (store.isCurrentSession()) { setRefreshRequired(true); setError('Your change is saved. Refresh to see the latest.') } }
  }

  const save = async () => {
    if (uploading || busy) return
    setBusy(true)
    setError(''); setConflict(null)
    try {
      const saved = await api.put<{ draft: Record<string, unknown>; lockVersion: number }>(`/applications/${app.id}/draft`, { data, lock_version: lockVersion })
      if (!store.isCurrentSession()) return
      setData(saved.draft); setLockVersion(saved.lockVersion)
      setDirty(false)
      store.notify('Draft saved')
      await refreshAfterWrite()
    } catch (cause) { reportFailure(cause) } finally {
      setBusy(false)
    }
  }

  const complete = async () => {
    if (uploading || busy || completed || !ownerActive) return
    setAttempted(true)
    if (!result.canComplete) return
    const destination = result.repeats ? 'save this medical attempt and plan another medical'
      : result.closes ? `close this order for the candidate: ${result.closes.label}`
      : def.no < STAGES.length ? `move to stage ${def.no + 1}: ${stageByNo(def.no + 1).title}`
      : 'mark this candidate as Deployed'
    if (!window.confirm(`Save your feedback for ${def.title} and ${destination}?`)) return
    setBusy(true)
    setError(''); setConflict(null)
    try {
      // The server validates and records exactly these values in one transaction.
      await api.post(`/applications/${app.id}/complete`, { data: { ...data, feedback: String(data.feedback).trim() }, lock_version: lockVersion })
      if (!store.isCurrentSession()) return
      setDirty(false)
      setCompleted(true)
      store.notify(result.repeats ? 'Medical result saved. A follow-up task was created.' : result.closes ? 'This order is closed for the candidate. Everything stays on record.' : 'Stage done')
      await refreshAfterWrite()
    } catch (cause) { reportFailure(cause) } finally {
      setBusy(false)
    }
  }

  // Short fields (Yes/No, date, time, choices) sit side by side on a 12-column grid; long ones take the whole row.
  const visibleFields = def.fields.filter((f) => f.name !== 'feedback' && fieldVisible(f, data))
  const fieldSpan = fieldSpans(visibleFields)
  const ready = result.canComplete && !result.closes && !result.repeats
  const checkState = result.canComplete ? (result.closes ? 'is-closes' : 'is-ready') : ''

  return (
    <div>
      {error && <div role="alert" className="sm-alert">
        <p>{error}</p>
        {conflict && <div className="sm-alert-actions">
          {conflict.current_stage === app.currentStage && conflict.status === 'active' && <Button size="sm" onClick={() => { setData(conflict.draft ?? {}); setLockVersion(conflict.lock_version ?? lockVersion); setDirty(false); setConflict(null); setError('') }}>Use the saved version</Button>}
          <Button size="sm" onClick={() => void store.refresh({ keepCurrent: true }).catch(reportFailure)}>Refresh</Button>
        </div>}
        {refreshRequired && !completed && <Button className="mt-3" size="sm" disabled={busy} onClick={() => void refreshAfterWrite()}>Refresh</Button>}
      </div>}
      {completed && <div role="status" className="sm-alert is-info">Saved. <button className="underline" onClick={() => void refreshAfterWrite()}>Refresh to see the history</button></div>}

      <section className="sm-card sm-current" aria-label={`Current stage ${def.no}: ${def.title}`}>
        <header>
          <div className="min-w-0">
            <p className="sm-current-label"><CircleDot className="h-3.5 w-3.5" aria-hidden="true" />Current stage · {def.no} of {STAGES.length}</p>
            <h3>{def.title}</h3>
            {!compact && <p>{def.summary}</p>}
          </div>
          {task && !compact && (
            <div className="sm-sla">
              <SlaMeter startedAt={taskClockStart(task)} title={task.title} due={dateTimeLabel(task.dueAt)} />
            </div>
          )}
        </header>

        {(!compact || attempted) && <div className={cn('sm-check', checkState)} aria-live="polite">
          <span className="sm-check-icon" aria-hidden="true">{result.closes ? <AlertTriangle className="h-4 w-4" /> : ready || result.repeats ? <CheckCircle2 className="h-4 w-4" /> : <ListChecks className="h-4 w-4" />}</span>
          <div className="min-w-0">
            <b>{result.canComplete ? 'Ready to save feedback and move' : 'Write feedback before moving this stage'}</b>
            {result.missing.length > 0
              ? <ul>{result.missing.map(item => <li key={item}>{item}</li>)}</ul>
              : <p>{result.repeats ? 'This saves the medical result and sets up the next medical task.' : result.closes ? `This closes the order for this candidate: ${result.closes.label}. Work done so far stays on record.` : def.no < STAGES.length ? `Next: stage ${def.no + 1}, ${stageByNo(def.no + 1).title}. A new task is created when you finish this stage.` : 'This finishes the deployment and adds one to the order’s filled openings.'}</p>}
          </div>
        </div>}

        <div className="sm-feedback">
          <StageFieldInput field={def.fields.find((field) => field.name === 'feedback')!} value={data.feedback} onChange={(value) => set('feedback', value)} disabled={!canWork || busy || completed} applicationId={app.id} versions={[]} stage={app.currentStage} onUploaded={onUploaded} onUploading={() => {}} />
          {!compact && <p className="sm-hint">Only you move the stage. Missing documents will stay pending in the document checklist.</p>}
        </div>

        <details className="sm-stage-details">
          <summary>Outcome and additional details <span>Optional</span></summary>
          <div className="sm-form">
          {visibleFields.map((f, index) => (
            <div key={f.name} className={`sm-c${fieldSpan[index]}`}>
              <StageFieldInput
                field={f}
                value={data[f.name]}
                onChange={(v) => set(f.name, v)}
                disabled={!canWork || busy || completed}
                applicationId={app.id}
                versions={applicationFileVersions(files, app, app.currentStage, f.name)}
                stage={app.currentStage}
                onUploaded={onUploaded}
                onUploading={(active) => setUploadingFields((current) => active ? [...new Set([...current, f.name])] : current.filter((name) => name !== f.name))}
              />
            </div>
          ))}
          </div>
        </details>

        {def.closer && (!compact || result.closes) && (
          <p className="sm-form-note">
            <AlertTriangle className="h-4 w-4" aria-hidden="true" />
            <span>If this stage ends with a closing result, the order closes for the candidate. They go back to the Data Bank and can be matched to another order.</span>
          </p>
        )}

        {attempted && result.missing.length > 0 && (
          <p className="sm-still">
            Still needed: {result.missing.join(', ')}.
          </p>
        )}

        <footer className="sm-foot">
          <span className="sm-foot-meta">
            {compact ? 'Feedback stays in this stage’s history.' : <>Save draft keeps your feedback here. {result.closes ? 'Save and close records your feedback and closes the order.' : 'Save and move records your feedback and advances only after you confirm.'}{def.repeatable && ' This stage can be done again.'}</>}
          </span>
          <div className="sm-foot-actions">
            {uploading ? <span role="status" className="sm-unsaved">Uploading files…</span> : dirty && <span className="sm-unsaved">Changes not saved</span>}
            <Button variant="secondary" onClick={save} disabled={!canWork || !dirty || busy || uploading || completed || refreshRequired}>
              {busy ? 'Saving…' : 'Save draft'}
            </Button>
            <Button
              variant={result.closes ? 'danger' : 'primary'}
              onClick={complete}
              disabled={!canWork || busy || uploading || completed || refreshRequired || !ownerActive}
              icon={result.closes ? <CircleSlash className="h-4 w-4" /> : <Check className="h-4 w-4" />}
            >
              {busy ? 'Saving…' : result.repeats ? 'Save feedback and plan another medical' : result.closes ? 'Save and close order' : def.no === STAGES.length ? 'Save and mark Deployed' : 'Save and move'}
            </Button>
          </div>
        </footer>
      </section>

      {/* Daily remarks live beside the stage, because that is where staff are working. */}
      {task && !compact && (
        <section className="sm-card sm-remarks" aria-label="Daily updates">
          <header>
            <h4>Daily updates</h4>
            {!store.updatedToday(task.id) && (
              <Badge tone="amber" variant="dot">Not updated today</Badge>
            )}
          </header>
          <div className="sm-remarks-body">
            <div className="sm-remarks-form">
              <Textarea
                rows={2}
                value={remark}
                onChange={(e) => setRemark(e.target.value)}
                placeholder="What happened today? e.g. Waiting for client feedback"
                maxLength={10000}
                disabled={!canWork || remarkBusy || completed}
              />
              <Button
                variant="secondary"
                onClick={async () => {
                  if (!remark.trim() || remarkBusy) return
                  setRemarkBusy(true)
                  try { await addRemark(task.id, remark.trim()); if (store.isCurrentSession()) setRemark('') }
                  catch (cause) { reportFailure(cause) }
                  finally { setRemarkBusy(false) }
                }}
                disabled={!canWork || !remark.trim() || remarkBusy || completed}
                icon={<MessageSquarePlus className="h-4 w-4" />}
                className="shrink-0"
              >
                {remarkBusy ? 'Saving…' : 'Add note'}
              </Button>
            </div>
            <p className="sm-hint">Notes show the time they were added and cannot be changed or deleted. Add one every day for each open task.</p>

            {remarks.length > 0 && (
              <ul className="sm-remarks-list">
                {remarks.map((r) => (
                  <li key={r.id}>
                    <p>{r.body}</p>
                    <small>{store.user(r.authorId)?.name} · {smartTime(r.createdAt)}</small>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      )}
    </div>
  )
}

function StageFieldInput({
  field,
  value,
  onChange,
  disabled,
  applicationId,
  stage,
  versions,
  onUploaded,
  onUploading,
}: {
  field: StageField
  value: unknown
  onChange: (v: unknown) => void
  disabled?: boolean
  applicationId?: string
  stage: number
  versions: FileVersion[]
  onUploaded: (file: ApplicationFile) => void
  onUploading: (active: boolean) => void
}) {
  const store = useStore()
  const [uploading, setUploading] = useState(false)

  // Stored as the person's name, so a completed stage still reads correctly if the
  // account is later deactivated or renamed.
  const options =
    field.optionsSource === 'users'
      ? store.users
          .filter((u) => u.active && u.role === 'staff')
          .map((u) => ({ value: u.id, label: u.name }))
      : field.optionsSource === 'currencies'
        // The agency's active currencies from the database, plus the one already saved here even if it was retired since.
        ? currencyChoices(store.settings.currencies, value as string).map((c) => ({ value: c.code, label: currencyLabel(c) }))
        : (field.options ?? [])

  // Rows written before this field became a picker hold a bare name — match on it so
  // the existing selection still shows.
  const selectedValue =
    field.optionsSource === 'users'
      ? userRefId(value) ||
        (store.users.find((u) => u.name === userRefName(value))?.id ?? '')
      : field.optionsSource === 'currencies'
        ? ((value as string) || store.settings.defaultAdvanceCurrency)
        : ((value as string) ?? '')

  switch (field.type) {
    case 'radio':
      return (
        <Field label={field.label} hint={field.help} required={field.required}>
          <RadioRow
            name={field.name}
            value={value as string}
            options={field.options ?? []}
            onChange={onChange}
            disabled={disabled}
          />
        </Field>
      )
    case 'select':
      return (
        <Field label={field.label} hint={field.help} required={field.required}>
          <Select
            value={selectedValue}
            onChange={(e) => {
              if (field.optionsSource !== 'users') return onChange(e.target.value)
              const u = store.users.find((x) => x.id === e.target.value)
              onChange(u ? ({ id: u.id, name: u.name } satisfies UserRef) : '')
            }}
            disabled={disabled}
          >
            <option value="">
              {field.optionsSource === 'users' ? 'Choose a staff member' : 'Choose one'}
            </option>
            {options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </Field>
      )
    case 'textarea':
      return (
        <Field label={field.label} hint={field.help} required={field.required}>
          <Textarea
            value={(value as string) ?? ''}
            placeholder={field.placeholder}
            onChange={(e) => onChange(e.target.value)}
            maxLength={field.name === 'feedback' ? 10000 : undefined}
            aria-required={field.required || undefined}
            disabled={disabled}
          />
        </Field>
      )
    case 'file':
      return (
        <FileDrop
          label={field.required ? `${field.label} (required)` : field.label}
          help={field.help}
          value={value as StoredFile | null}
          versions={versions}
          uploaderName={(id) => store.user(id)?.name || 'Former team member'}
          busy={uploading}
          disabled={disabled}
          onFile={async (f) => {
            if (!applicationId) return
            setUploading(true)
            onUploading(true)
            try {
              const slot = applicationFileSlot(stage, field.name)
              const up = await api.upload('application', applicationId, slot, f)
              // Keep the identifier in the draft, not the bytes, so saving the stage
              // never re-sends the file.
              onChange({ name: up.name, sizeKb: up.sizeKb, id: up.id, url: up.url, version: up.version })
              onUploaded({ ...up, slot, uploadedBy: store.me.id })
            } finally {
              setUploading(false)
              onUploading(false)
            }
          }}
        />
      )
    case 'checkbox':
      return (
        <div className="pt-1">
          <Checkbox
            checked={Boolean(value)}
            onChange={onChange}
            label={field.label}
            disabled={disabled}
          />
        </div>
      )
    case 'date':
      return (
        <Field label={field.label} hint={field.help} required={field.required}>
          <DatePicker value={(value as string) ?? ''} onChange={onChange} disabled={disabled} />
        </Field>
      )
    default:
      return (
        <Field label={field.label} hint={field.help} required={field.required}>
          <Input
            type={field.type === 'number' ? 'number' : field.type === 'time' ? 'time' : 'text'}
            ident={field.ident}
            placeholder={field.placeholder}
            value={(value as string) ?? ''}
            onChange={(e) =>
              onChange(field.type === 'number' ? Number(e.target.value) || '' : e.target.value)
            }
            disabled={disabled}
          />
        </Field>
      )
  }
}

/* ----------------------------------------------------------- Terminal rows */

function DeployedRow() {
  return (
    <section className="sm-card sm-end is-deployed animate-stamp-in">
      <span className="sm-end-icon" aria-hidden="true"><Trophy className="h-5 w-5" /></span>
      <div>
        <b>Deployed</b>
        <p>All 11 stages are done. The order's deployed count went up by one. When deployed equals openings, the order becomes Filled.</p>
      </div>
    </section>
  )
}

function ClosedRow({ app }: { app: Application }) {
  const notInterested = app.closureReason === 'not_interested' || app.closureReason === 'resume_not_received'
  return <section className="sm-card sm-end is-closed">
    <span className="sm-end-icon" aria-hidden="true"><CircleSlash className="h-5 w-5" /></span>
    <div>
      <b>Closed: {app.closureReason ? closureLabels[app.closureReason] : 'Closed'}</b>
      <p>{app.closureReason === 'unmapped'
        ? 'The candidate was removed from this order and its open tasks were cancelled. They can be matched to another order.'
        : notInterested
          ? 'The candidate is marked Not interested. They stay in the Not interested group and can be offered other orders.'
          : 'The candidate is back in the Data Bank and can be matched to another order.'} Finished stages and files are above. An admin can reopen a finished stage, and must give a reason.</p>
    </div>
  </section>
}
