import { useEffect, useMemo, useRef, useState } from 'react'
import {
  BookUser,
  Briefcase,
  Building2,
  Check,
  CircleDot,
  CircleSlash,
  Lock,
  MessageSquarePlus,
  RotateCcw,
  User,
} from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { ApplicationFormButton } from '@/features/application-form/components/application-form-button'
import { currencyChoices, currencyLabel, homeDigits } from '@/shared/lib/agency'
import { formatPhone } from '@/shared/lib/phone'
import { dateLabel, dateTimeLabel, money, smartTime } from '@/shared/lib/format'
import { applicationFileSlot, applicationFileVersions, newestFileVersions, type ApplicationFile } from '@/shared/lib/document-files'
import { useStore } from '@/shared/state/crm-store'
import { RowMenu } from '@/shared/ui/table'
import { DatePicker } from '@/shared/ui/date-picker'
import { ApiError, api } from '@/shared/api/client'
import { taskClockStart } from '@/shared/lib/task-time'
import {
  ADVANCE_STAGE,
  PASSPORT_STAGE,
  PCC_STAGE,
  STAGES,
  TOTAL_STAGES,
  closureLabels,
  closureTab,
  fieldVisible,
  isPendingReply,
  stageByNo,
  userRefId,
  userRefName,
  type StageField,
  type UserRef,
} from '@/shared/workflow/stages'
import { advanceSummary, withAdvanceStatus } from '@/shared/workflow/advance'
import type { Application, FileVersion, StageRecord } from '@/shared/model/types'
import { Dialog } from '@/shared/ui/overlay'
import { ReopenApplication } from './reopen-application'
import { canReopenApplication } from '../reopen-application'
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
import { refreshSilently, refreshUntil } from '@/shared/state/refresh-retry'
import { useCandidateDocuments } from '@/features/candidates/use-candidate-documents'
import { SlaMeter, StageRail } from './stage-rail'
import { agentName } from '@/features/candidates/data-bank-search'
import { canWorkCandidate, workRefusal } from '@/features/candidates/candidate-access'
import { StageReplies, stageReplyPairs } from './stage-replies'
import '@/styles/candidate-v2.css'
import '@/styles/stage-modal.css'
import '@/styles/touch-targets.css'

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
  onStageRecorded,
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
  /** Told the moment the server has recorded a stage (before the workspace is re-read): a list behind the window (Tasks) re-reads at once. Unlike `onStageChanged` it never closes or reloads the caller. */
  onStageRecorded?: () => void
}) {
  const store = useStore()
  const snapshot = useWorkspaceReady(open && applicationId ? `stage:${applicationId}` : null)
  useEffect(() => {
    if (embedded || !open || !focusStage || !snapshot.ready) return
    const timer = window.setTimeout(() => {
      const section = document.querySelector<HTMLElement>(`[data-sm-stage="${focusStage}"]`)
      if (!section) return
      section.scrollIntoView({ block: 'start', behavior: 'smooth' })
    }, 80)
    return () => window.clearTimeout(timer)
  }, [open, focusStage, snapshot.ready, applicationId, embedded])
  const app = store.applications.find((a) => a.id === applicationId)
  // Who reopened an earlier try, and when. A failed read is not an error: the reason is still shown.
  const [reopenings, setReopenings] = useState<Reopening[]>([])
  const reopenCount = app ? app.stages.filter((record) => record.reopenedReason).length : 0
  useEffect(() => {
    if (!embedded || !open || !applicationId || reopenCount === 0) { setReopenings([]); return }
    let current = true
    api.get<Reopening[]>(`/applications/${encodeURIComponent(applicationId)}/reopenings`)
      .then((rows) => { if (current && Array.isArray(rows)) setReopenings(rows) })
      .catch(() => { if (current) setReopenings([]) })
    return () => { current = false }
  }, [embedded, open, applicationId, reopenCount])
  const cand = app ? store.candidate(app.candidateId) : undefined
  const canWork = canWorkCandidate(store.me, cand)
  const [files, setFiles] = useState<ApplicationFile[]>([])
  const [fileError, setFileError] = useState(false)
  const [fileRetry, setFileRetry] = useState(0)
  const [dirty, setDirty] = useState(false)
  const [pending, setPending] = useState(false)
  const [reopening, setReopening] = useState(false)
  const [selectedHistory, setSelectedHistory] = useState<number | null>(null)
  useEffect(() => { setSelectedHistory(null) }, [applicationId, open, app?.currentStage])

  useEffect(() => { reportDirty?.(dirty); return () => reportDirty?.(false) }, [dirty, reportDirty])
  useEffect(() => { reportBusy?.(pending || reopening); return () => reportBusy?.(false) }, [pending, reopening, reportBusy])

  useEffect(() => {
    if (!open || !applicationId) return
    let live = true
    setFiles([])
    setFileError(false)
    if (!canWork) return
    api.get<ApplicationFile[]>(`/files?entity_type=application&entity_id=${encodeURIComponent(applicationId)}`)
      .then((rows) => { if (live) setFiles((current) => [...rows, ...current.filter((file) => !rows.some((row) => row.id === file.id))]) })
      .catch(() => { if (live) setFileError(true) })
    return () => { live = false }
  }, [applicationId, open, fileRetry, canWork])

  const order = app ? store.order(app.orderId) : undefined
  const client = order ? store.company(order.companyId) : undefined
  const designation = order ? store.designation(order.designationId) : undefined
  // A placement that was JUST created (the client and job were saved a moment ago) is on the server before it is in this browser's
  // workspace snapshot. That is never a "Refresh" job for the person: show "Loading the stages…" and re-read by ourselves
  // (0.3, 0.6, 1.2, 2.4 s, then every 5 s); only after 30 s say plainly that it could not be loaded, with "Try again".
  const stagesLoaded = Boolean(app && cand && order && client && designation)
  const stagesLoadedRef = useRef(stagesLoaded)
  stagesLoadedRef.current = stagesLoaded
  const waitForStages = open && Boolean(applicationId) && !stagesLoaded && (snapshot.ready || Boolean(snapshot.error))
  const [stagesGaveUp, setStagesGaveUp] = useState(false)
  const [stagesRound, setStagesRound] = useState(0)
  useEffect(() => {
    if (!waitForStages) { setStagesGaveUp(false); return undefined }
    let cancelled = false
    setStagesGaveUp(false)
    void refreshUntil({ read: () => store.refresh({ keepCurrent: true }), done: () => stagesLoadedRef.current, cancelled: () => cancelled })
      .then((outcome) => { if (!cancelled && outcome === 'gave_up') setStagesGaveUp(true) })
    return () => { cancelled = true }
  }, [waitForStages, applicationId, stagesRound])
  if (!snapshot.ready || !app || !cand || !order || !client || !designation) {
    const failed = stagesGaveUp
    const message = failed
      ? (snapshot.error || 'We could not load the stages for this application yet. Check your connection, then try again.')
      : 'Loading the stages…'
    const state = <div className="sm sm-state"><p role={failed ? 'alert' : 'status'} className="text-sm text-ink-600">{message}</p>{failed && <Button className="mt-4" variant="primary" onClick={() => { setStagesRound((value) => value + 1); snapshot.retry() }}>Try again</Button>}</div>
    return embedded ? state : <Dialog open={open} onClose={onClose} title="Stages for this order" hideHeading>{state}</Dialog>
  }
  const owner = store.user(cand.ownerId)

  const readOnlyReason = workRefusal(store.me, cand, owner?.name)
  const effective = app.stages.filter((record) => !record.reopenedReason)
  const live = app.status === 'active' ? app.currentStage : null

  const editor = <CurrentStage key={`${app.id}-${app.currentStage}-${app.stages.length}`} app={app} canWork={canWork && !reopening} onDirtyChange={setDirty} onBusyChange={setPending} files={files} onUploaded={(file) => setFiles((items) => [...items.filter((item) => item.id !== file.id), file])} onStageChanged={onStageChanged} onStageRecorded={onStageRecorded} compact />

  if (embedded) {
    if (!open) return null
    const selectedStage = stageByNo(focusStage ?? app.currentStage) ?? stageByNo(app.currentStage)
    const selectedRecords = app.stages.filter((record) => record.stage === selectedStage.no).sort((a, b) => b.attempt - a.attempt)
    const activeRecords = selectedRecords.filter((record) => !record.reopenedReason)
    const current = live === selectedStage.no
    return <div className="sm sm-embedded" data-sm-stage={selectedStage.no}>
      {!canWork && <p className="sm-note"><Lock className="h-4 w-4" aria-hidden="true" />{readOnlyReason}</p>}
      {!owner?.active && <p className="sm-note">Assign an active recruiter before moving or reopening a stage. You can still save a draft.</p>}
      {app.status === 'closed' && selectedStage.no === app.currentStage && <ClosedRow app={app} canReopen={canReopenApplication(app.status, canWork)} disabled={!owner?.active || pending || reopening} onRecorded={onStageRecorded} onDone={onStageChanged} />}
      {fileError && <p className="sm-alert" role="alert">We could not load the documents. Feedback is still available. <button type="button" className="underline" onClick={() => setFileRetry((value) => value + 1)}>Retry documents</button></p>}
      {current ? editor
        : <section className="sm-card sm-inline-history">
          <header className="sm-inline-title"><div><small>Stage {selectedStage.no} of {STAGES.length}</small><h3>{selectedStage.title}</h3></div>{activeRecords.length > 0 && <Badge tone="green" variant="dot">Saved history</Badge>}</header>
          {selectedRecords.map((record) => <div className="sm-inline-attempt" key={`${record.stage}-${record.attempt}`}>{record.reopenedReason && <ReopenedNote record={record} reopenings={reopenings} />}<CompletedStage record={record} files={files} app={app} /></div>)}
          {canWork && !(app.status === 'closed' && selectedStage.no === app.currentStage) && activeRecords.length > 0 && <ReopenStage app={app} stage={selectedStage.no} disabled={pending || reopening || !owner?.active} onBusyChange={setReopening} beforeReopen={() => !dirty || window.confirm('Discard unsaved feedback and reopen this stage?')} onStageChanged={onStageChanged} />}
        </section>}
      {current && selectedRecords.length > 0 && <details className="sm-extra"><summary>Earlier feedback and attempts ({selectedRecords.length})</summary><div className="mt-4 space-y-6">{selectedRecords.map((record) => <div key={`${record.stage}-${record.attempt}`}>{record.reopenedReason && <ReopenedNote record={record} reopenings={reopenings} />}<CompletedStage record={record} files={files} app={app} /></div>)}</div></details>}
    </div>
  }

  return (
    <Dialog
      open={open}
      onClose={() => {
        if (!pending && !reopening && (!dirty || window.confirm('Discard unsaved stage changes?'))) onClose()
      }}
      width="full"
      title="Stages for this order"
      hideHeading
    >
      <div className="sm">
        <div className="sm-wrap">
          <ApplicationHeader app={app} effective={effective} />

          <ol className="sm-timeline" aria-label="Recruitment stages">
            {STAGES.map(stage => {
              const latest = effective.filter(record => record.stage === stage.no)
                .reduce<StageRecord | undefined>((last, record) => !last || record.attempt > last.attempt ? record : last, undefined)
              const negative = latest && (NEGATIVE_OUTCOMES.includes(latest.outcome) || (app.status === 'closed' && stage.no === app.currentStage))
              const current = stage.no === live
              const answer = latest ? stageAnswer(stage, latest.data, latest.outcome) : ''
              const draftAnswer = current ? stageAnswer(stage, app.draft ?? {}) : ''
              const schedule = stage.key === 'interview_scheduling' ? (current ? app.draft : latest?.data) : undefined
              const interviewDate = schedule?.scheduled !== 'no' && !schedule?.stop && typeof schedule?.date === 'string' ? schedule.date : ''
              const title = latest && !negative && stage.key === 'prescreening' ? 'Prescreened' : stage.title
              return <li key={stage.no} data-sm-stage={stage.no} className={cn('sm-step', current ? 'is-current' : negative ? 'is-closed' : latest ? 'is-done' : 'is-upcoming')}>
                <span className="sm-node" aria-hidden="true">{latest && !negative ? <Check className="h-4 w-4" /> : stage.no}</span>
                <div className="sm-card sm-monitor-row">
                  <div>{latest && !current ? <button type="button" className="sm-history-select"
                    aria-expanded={selectedHistory === stage.no} aria-controls={`sm-history-${stage.no}`}
                    onClick={() => setSelectedHistory(selectedHistory === stage.no ? null : stage.no)}>
                    <b>{title}</b><span aria-hidden="true">{selectedHistory === stage.no ? '\u25B4' : '\u25BE'}</span>
                  </button> : <b>{title}</b>}
                  </div>
                  <div className="sm-monitor-answers">
                    <div className="sm-result-stack">
                    {answer && <Badge tone={negative ? 'stamp' : greenStageAnswer(stage.no, answer, latest?.data) ? 'green' : 'neutral'}>{answer}</Badge>}
                    {current && <Badge tone={draftAnswer && greenStageAnswer(stage.no, draftAnswer, app.draft) ? 'green' : 'navy'}>{draftAnswer || 'In progress'}</Badge>}
                      {interviewDate && <div className="sm-interview-date">Interview date <time dateTime={interviewDate}>{dateLabel(interviewDate)}{typeof schedule?.time === 'string' && schedule.time && `, ${interviewTimeLabel(schedule.time)}`}</time></div>}
                    </div>
                    {latest?.completedAt && <time className="tnum" dateTime={latest.completedAt}>{dateTimeLabel(latest.completedAt)}</time>}
                    {latest && !current && canWork && !(app.status === 'closed' && stage.no === app.currentStage) &&
                      <ReopenStage inMenu app={app} stage={stage.no} disabled={!owner?.active || pending || reopening}
                        onBusyChange={setReopening} beforeReopen={() => !dirty || window.confirm('Discard unsaved stage changes?')}
                        onStageChanged={() => { onStageRecorded?.(); onStageChanged?.() }} />}
                    {stage.no === app.currentStage && closureTab(app.closureReason) === 'Declined' && canReopenApplication(app.status, canWork) &&
                      <ReopenApplication app={app} inMenu stageTitle={stage.title} disabled={!owner?.active || pending || reopening}
                        onRecorded={onStageRecorded} onDone={onStageChanged} />}
                  </div>
                </div>
                {latest && !current && <div id={`sm-history-${stage.no}`} hidden={selectedHistory !== stage.no}>
                  {selectedHistory === stage.no && <div className="sm-card sm-inline-history">
                    <CompletedStage record={latest} files={files} app={app} />
                  </div>}
                </div>}
                {current && canWork && <div className="sm-embedded">
                  {!owner?.active && <p className="sm-note">Assign an active recruiter before moving this stage. You can still save a draft.</p>}
                  {fileError && <button type="button" className="underline" onClick={() => setFileRetry(value => value + 1)}>Retry documents</button>}
                  {editor}
                </div>}
              </li>
            })}
          </ol>
        </div>
      </div>
    </Dialog>
  )
}

function greenStageAnswer(stage: number, answer: string, data?: Record<string, unknown> | null) {
  if (stageReplyPairs[stage]?.choices.some(([label]) => label === answer)) return true
  if (typeof data?.feedback === 'string' && data.feedback.trim() === answer) return true
  return stage <= 3 || (stage === 4 && answer === 'Selected') || (stage === 5 && answer === 'Agreed')
}

function stageAnswer(stage: typeof STAGES[number], data: Record<string, unknown>, outcome = ''): string {
  if (typeof data.customReply === 'string' && data.customReply.trim()) return data.customReply.trim()
  const stop = stage.stops?.find(option => option.value === data.stop)
  if (stop && data.stop !== 'declined') return stop.label
  const pair = stageReplyPairs[stage.no]
  const saved = pair?.choices.find(([, value]) => data[pair.field] === value)
  if (saved) return saved[0]
  if (stop) return stop.label
  if (stage.no <= 3) {
    const primary = stage.fields.find(field => field.type === 'radio')
    const selected = primary?.options?.find(option => option.value === data[primary.name])
    if (selected) return selected.label
  }
  const decision = stage.fields.find(field => field.name === stage.decision?.field)
  const selected = decision?.options?.find(option => option.value === data[decision.name])
  if (selected) return selected.label
  if (stage.no === ADVANCE_STAGE) {
    const status = withAdvanceStatus(data).advanceStatus
    if (status === 'collected') return 'Received'
    if (status === 'pending') return 'Not received'
  }
  if (outcome && !['Completed manually', 'Completed'].includes(outcome)) return outcome
  for (const field of stage.fields) {
    const choice = field.options?.find(option => option.value === data[field.name])
    if (choice) return ['yes', 'no'].includes(choice.value) ? `${field.label}: ${choice.label}` : choice.label
  }
  return stage.no > 3 && typeof data.feedback === 'string' ? data.feedback.trim() : ''
}

const NEGATIVE_OUTCOMES = ['Failed', 'Rejected', 'Declined', 'Not received', 'Unfit', 'Not forwarded']

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

function ApplicationHeader({ app, effective }: { app: Application; effective: StageRecord[] }) {
  const store = useStore()
  const cand = store.candidate(app.candidateId)!
  const order = store.order(app.orderId)!
  const client = store.company(order.companyId)!
  const designation = store.designation(order.designationId)!
  const owner = store.user(cand.ownerId)
  const referrer = agentName(cand)
  const referringLabel = /^care\s*india(?:\s+overseas)?(?:\s+(?:pvt\.?|private)\s*(?:ltd\.?|limited))?$/i.test(referrer) ? 'Care India' : referrer
  const country = order.country?.trim() || client.country?.trim() || store.client(client.clientId)?.country?.trim() || ''
  const facts: [string, string, typeof User, string][] = [
    ['company', 'Company name', Building2, client.name],
    ['job', 'Job title', Briefcase, designation.title],
    ['agent', 'Referring agent', User, referringLabel],
    ['recruiter', 'Recruiter', User, owner?.name ?? ''],
    ['country', 'Country', Building2, country],
    ['passport', 'Passport number', BookUser, cand.passportNo || ''],
    ['expiry', 'Passport expiry', BookUser, cand.passportExpiry ? dateLabel(cand.passportExpiry) : ''],
  ]
  const statusTone = app.status === 'closed' ? 'stamp' : app.status === 'deployed' ? 'green' : 'navy'
  const statusLabel = app.status === 'active' ? 'Active' : app.status === 'deployed' ? 'Deployed' : 'Closed'
  const progressLead = app.status === 'active' ? `Stage ${app.currentStage} of ${STAGES.length} · ${stageByNo(app.currentStage).title}` : app.status === 'deployed' ? 'Deployed' : `Closed at stage ${app.currentStage}${app.closureReason ? ` · ${closureLabels[app.closureReason]}` : ''}`
  return (
    <>
      <header className="sm-head">
        <div className="sm-head-main">
          <div className="sm-id">
            <h3 className="sm-name" title={cand.name}>{cand.name}</h3>
            {cand.phone && <p className="sm-sub"><b className="tnum">{formatPhone(cand.phone, homeDigits(store.settings.homeCallingCode))}</b></p>}
          </div>
          <div className="sm-head-side">
            <Badge tone={statusTone} variant="dot">{statusLabel}</Badge>
          </div>
        </div>
        <dl className="sm-facts">
          {facts.map(([area, label, Icon, value]) => <div key={area} style={{ gridArea: area }}>
            <dt><Icon className="h-3.5 w-3.5" aria-hidden="true" />{label}</dt>
            <dd title={value}>{value}</dd>
          </div>)}
        </dl>
        <div className="sm-progress">
          <div className="sm-progress-line">
            <p className="sm-progress-label"><b>{progressLead}</b></p>
          </div>
          <StageRail status={app.status} currentStage={app.currentStage} reached={[...new Set(effective.map(item => item.stage))]} numbered />
        </div>
        <div className="sm-head-foot">
          <ApplicationFormButton size="sm" variant="primary" candidateId={cand.id} orderId={order.id} />
        </div>
      </header>
    </>
  )
}

/* -------------------------------------------------------- Completed stage */

function CompletedStage({ record, files, app }: { record: StageRecord; files: ApplicationFile[]; app: Application }) {
  const store = useStore()
  const def = stageByNo(record.stage)
  const negative = NEGATIVE_OUTCOMES.includes(record.outcome)
  // An advance saved before it had a status reads as Collected when it holds an amount; the same words the tracker sheet uses.
  const data = record.stage === ADVANCE_STAGE ? withAdvanceStatus(record.data) : record.data
  const answer = stageAnswer(def, data, record.outcome)
  const advanceLine = record.stage === ADVANCE_STAGE ? advanceSummary(data, store.settings.defaultAdvanceCurrency) : null

  return (
    <section>
      <header className="sm-attempt-head">
        {answer && <Badge tone={negative ? 'stamp' : 'green'}>{answer}</Badge>}
        <time dateTime={record.completedAt}>{dateTimeLabel(record.completedAt)}</time>
      </header>

      <dl className="sm-record-fields">
        {advanceLine && (
          <div className="sm-fact is-wide">
            <dt>Advance</dt>
            <dd>{advanceLine}</dd>
          </div>
        )}
        {def.fields
          .filter((f) => fieldVisible(f, data) && data[f.name] != null && data[f.name] !== '')
          .map((f) => (
            <div key={f.name} className={cn('sm-fact', (f.type === 'file' || f.type === 'textarea') && 'is-wide')}>
              <dt>{f.label}</dt>
              <dd className={cn(f.ident && 'ident', f.type === 'textarea' && 'whitespace-pre-line')}>
                {f.type === 'file' && (data[f.name] as StoredFile).url ? (
                  <FileDrop value={data[f.name] as StoredFile} versions={applicationFileVersions(files, app, record.stage, f.name)} disabled uploaderName={(id) => store.user(id)?.name || 'Former team member'} />
                ) : (
                  readValue(f, data[f.name], data)
                )}
              </dd>
            </div>
          ))}
      </dl>


    </section>
  )
}

function ReopenStage({ app, stage, inMenu = false, disabled, onBusyChange, beforeReopen, onStageChanged }: { app: Application; stage: number; inMenu?: boolean; disabled: boolean; onBusyChange: (busy: boolean) => void; beforeReopen: () => boolean; onStageChanged?: () => void }) {
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
        <p className="text-[13px] leading-5 text-ink-600">This starts stage {stage} again, sets aside the results of any later stages, and replaces the task that is open now. Earlier records and files stay on record. Reopening a deployment also changes the order's deployed count.</p>
        <Field label="Reason for reopening" required><Textarea value={reason} onChange={(event) => setReason(event.target.value)} maxLength={2000} disabled={busy || disabled} placeholder="What needs to be corrected?" /></Field>
        <div className="flex flex-wrap gap-2"><Button variant="primary" disabled={busy || disabled || !reason.trim()} onClick={() => void reopen()}>{busy ? 'Reopening…' : `Reopen stage ${stage}`}</Button><Button disabled={busy || disabled} onClick={() => { setEditing(false); setError('') }}>Cancel</Button></div>
      </div> : inMenu ? <RowMenu label={`Actions for ${stageByNo(stage).title}`} items={[{
        label: 'Reopen stage', icon: RotateCcw, onSelect: () => setEditing(true), disabledReason: disabled ? 'Reopening is unavailable' : undefined,
      }]} /> : <Button variant="secondary" size="sm" disabled={disabled} icon={<RotateCcw className="h-4 w-4" />} onClick={() => setEditing(true)}>Reopen stage {stage}</Button>}
  </div>
}

function interviewHourLabel(hour: number) {
  return `${hour % 12 || 12} ${hour < 12 ? 'AM' : 'PM'}`
}

function interviewTimeLabel(time: string) {
  const [hour, minute] = time.split(':')
  return interviewHourLabel(Number(hour)).replace(' ', `:${minute} `) + ' IST'
}

function readValue(f: StageField, raw: unknown, row: Record<string, unknown> = {}): string {
  if (raw == null || raw === '') return '—'
  if (f.type === 'file') return (raw as StoredFile).name ?? 'Uploaded'
  if (f.type === 'checkbox') return raw ? 'Yes' : 'No'
  if (f.type === 'time') return interviewTimeLabel(String(raw))
  if (f.type === 'date') return dateLabel(raw as string)
  // The advance is shown in the currency it was collected in, not the order's.
  if (f.type === 'number')
    return f.name === 'amount' || f.name === 'serviceChargeTotal'
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
  onStageRecorded,
}: {
  app: Application
  canWork: boolean
  onDirtyChange: (dirty: boolean) => void
  onBusyChange: (busy: boolean) => void
  files: ApplicationFile[]
  onUploaded: (file: ApplicationFile) => void
  compact?: boolean
  onStageChanged?: () => void
  onStageRecorded?: () => void
}) {
  const store = useStore()
  const { addRemark } = store
  const def = stageByNo(app.currentStage)
  const ownerActive = !!store.user(store.candidate(app.candidateId)?.ownerId ?? '')?.active

  // Edits are held locally until Save. Nothing is written as you type, so the form has
  // a dirty state and leaving with unsaved work is a deliberate act, not an accident.
  const candidate = store.candidate(app.candidateId)
  const { documents: candidateDocuments } = useCandidateDocuments(candidate)
  const profilePassport = newestFileVersions(candidateDocuments.find((document) => document.key === 'passport')?.versions ?? [])[0]
  const profileAirTicket = newestFileVersions(candidateDocuments.find((document) => document.key === 'air_ticket')?.versions ?? [])[0]
  const profilePcc = newestFileVersions(candidateDocuments.find((document) => document.key === 'pcc')?.versions ?? [])[0]
  const profilePassportId = profilePassport?.id
  const profileAirTicketId = profileAirTicket?.id
  const profilePccId = profilePcc?.id
  // Stage 7 (PCC) starts from the PCC already on the profile and stage 8 (Passport) from the passport copy there; the recruiter may
  // replace either. Stage 12 likewise starts from the Air ticket already saved in the
  // candidate's Documents section. It is the same attachment, not a second
  // upload, and the server still requires a travel date before deployment.
  const withCandidateDocuments = (draft: Record<string, unknown>) => {
    let prepared = draft
    if (app.currentStage === PCC_STAGE && !prepared.file && profilePcc) {
      prepared = { ...prepared, pccStatus: prepared.pccStatus ?? 'received', file: { id: profilePcc.id, name: profilePcc.name, sizeKb: profilePcc.sizeKb, url: profilePcc.url, version: profilePcc.version } }
    }
    if (app.currentStage === PASSPORT_STAGE && !prepared.copy && profilePassport) {
      prepared = { ...prepared, copy: { id: profilePassport.id, name: profilePassport.name, sizeKb: profilePassport.sizeKb, url: profilePassport.url, version: profilePassport.version } }
    }
    if (app.currentStage === TOTAL_STAGES && !prepared.file && profileAirTicket) {
      prepared = { ...prepared, file: { id: profileAirTicket.id, name: profileAirTicket.name, sizeKb: profileAirTicket.sizeKb, url: profileAirTicket.url, version: profileAirTicket.version } }
    }
    // A stage-9 draft saved before the advance had a status shows it as Collected / Pending in the form.
    if (app.currentStage === ADVANCE_STAGE) prepared = withAdvanceStatus(prepared)
    return prepared
  }
  const [data, setData] = useState<Record<string, unknown>>(() => withCandidateDocuments(app.draft ?? {}))
  const [lockVersion, setLockVersion] = useState(app.lockVersion ?? 0)
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [uploadingFields, setUploadingFields] = useState<string[]>([])
  const uploading = uploadingFields.length > 0
  const pendingReply = isPendingReply(data.customReply)
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
  }, [app.lockVersion, app.currentStage, profilePassportId, profileAirTicketId, profilePccId])

  const task = store.tasks.find(
    (t) => t.applicationId === app.id && t.stage === app.currentStage && !t.completedAt && !t.cancelledAt,
  )
  const remarks = task ? store.remarksFor(task.id) : []
  const [remark, setRemark] = useState('')

  const set = (name: string, value: unknown) => {
    setData((d) => ({ ...d, [name]: value }))
    setDirty(true)
  }

  const applyReply = (reply: string) => {
    const pair = stageReplyPairs[def.no]
    if (!pair) return
    setData(current => ({
      ...current, [pair.field]: undefined, stop: undefined, customReply: reply,
      ...(def.no === ADVANCE_STAGE ? { collected: undefined } : {}),
    }))
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
    // Never a manual job: the re-read is retried silently (0.3 s ... 5 s apart); only after about 30 s of failures does the person see a message.
    const ok = await refreshSilently(() => store.refresh({ keepCurrent: true }), { cancelled: () => !store.isCurrentSession() })
    if (!store.isCurrentSession()) return
    if (ok) { setRefreshRequired(false); setError(''); onStageChanged?.() }
    else { setRefreshRequired(true); setError('Your change is saved, but the latest details could not be loaded yet. Check your connection, then try again.') }
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
      store.notify(pendingReply ? 'Saved as pending' : 'Draft saved')
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
      : result.closes ? `stop here and close this order for the candidate (${result.closes.label}). This candidate will not go to the next stage`
      : def.no < STAGES.length ? `move to stage ${def.no + 1}: ${stageByNo(def.no + 1).title}`
      : 'mark this candidate as Deployed'
    if (!window.confirm(`Confirm ${def.title} and ${destination}?`)) return
    setBusy(true)
    setError(''); setConflict(null)
    try {
      // The server validates and records exactly these values in one transaction.
      await api.post(`/applications/${app.id}/complete`, { data: { ...data, feedback: String(data.feedback ?? '').trim() || undefined }, lock_version: lockVersion })
      if (!store.isCurrentSession()) return
      // The stage is recorded: a list behind this window (Tasks) re-reads NOW, not after the whole workspace has been downloaded again below,
      // so a candidate who was rejected or declined leaves the Current process list while the person is still reading "Saved".
      onStageRecorded?.()
      setDirty(false)
      setCompleted(true)
      store.notify(result.repeats ? 'Medical result saved. A follow-up task was created.' : result.closes ? 'This order is closed for the candidate. Everything stays on record.' : 'Stage done')
      await refreshAfterWrite()
    } catch (cause) { reportFailure(cause) } finally {
      setBusy(false)
    }
  }

  const pair = stageReplyPairs[def.no]
  const visibleFields = def.fields.filter(field =>
    (def.no === 3 && data.scheduled === 'yes' && ['date', 'time'].includes(field.name)) ||
    (def.no === TOTAL_STAGES && data.ticketStatus === 'received' && ['travelDate', 'file'].includes(field.name)))
  const fieldSpan = fieldSpans(visibleFields)

  return (
    <div>
      {error && <div role="alert" className="sm-alert">
        <p>{error}</p>
        {conflict && <div className="sm-alert-actions">
          {conflict.current_stage === app.currentStage && conflict.status === 'active' && <Button size="sm" onClick={() => { setData(conflict.draft ?? {}); setLockVersion(conflict.lock_version ?? lockVersion); setDirty(false); setConflict(null); setError('') }}>Use the saved version</Button>}
          <Button size="sm" onClick={() => void store.refresh({ keepCurrent: true }).catch(reportFailure)}>Refresh</Button>
        </div>}
        {refreshRequired && !completed && <Button className="mt-3" size="sm" disabled={busy} onClick={() => void refreshAfterWrite()}>Try again</Button>}
      </div>}
      {completed && <div role="status" className="sm-alert is-info">Saved.{refreshRequired ? <> <button className="underline" onClick={() => void refreshAfterWrite()}>Try again</button></> : ' Updating the history…'}</div>}

      <section className="sm-card sm-current" aria-label={`Current stage ${def.no}: ${def.title}`}>
        <header>
          <div className="min-w-0">
            <p className="sm-current-label"><CircleDot className="h-3.5 w-3.5" aria-hidden="true" />Current stage · {def.no} of {STAGES.length}</p>
            <h3>{def.title}</h3>
          </div>
          {task && !compact && (
            <div className="sm-sla">
              <SlaMeter startedAt={taskClockStart(task)} title={task.title} due={dateTimeLabel(task.dueAt)} />
            </div>
          )}
        </header>

        <div className="sm-feedback">
          {canWork && <StageReplies key={`${store.me?.id}:${def.no}`} userId={store.me!.id} stage={def.no} disabled={busy || completed}
            status={pair && !data.customReply ? data[pair.field] : undefined} feedback={data.customReply} onSelect={(field, value) => {
              setData(previous => ({ ...previous, [field]: value, customReply: undefined, stop: pair?.choices[1][1] === value ? 'declined' : undefined,
                ...(field === 'advanceStatus' ? { collected: value === 'collected' ? 'yes' : 'no' } : {}) }))
              setDirty(true)
            }} onChoose={applyReply} />}
          {(canWork || Boolean(data.feedback)) && <details>
            <summary>{data.feedback ? 'Feedback' : 'Add feedback (optional)'}</summary>
            <Textarea aria-label="Stage feedback" rows={2} maxLength={10000}
              value={String(data.feedback ?? '')} onChange={event => set('feedback', event.target.value)}
              disabled={!canWork || busy || completed} />
          </details>}
        </div>

        {visibleFields.length > 0 && <div className="sm-form sm-feedback">
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
        </div>}

        {attempted && result.missing.length > 0 && (
          <p className="sm-still">
            Still needed: {result.missing.join(', ')}.
          </p>
        )}

        <footer className="sm-foot">
          <div className="sm-foot-actions">
            {uploading ? <span role="status" className="sm-unsaved">Uploading files…</span> : dirty && <span className="sm-unsaved">Changes not saved</span>}
            <Button variant={pendingReply ? "primary" : "secondary"} onClick={save} disabled={!canWork || !dirty || busy || uploading || completed || refreshRequired}>
              {busy ? 'Saving…' : pendingReply ? 'Save as pending' : 'Save draft'}
            </Button>
            {!pendingReply && <Button
              variant={result.closes ? 'danger' : 'primary'}
              onClick={complete}
              disabled={!canWork || busy || uploading || completed || refreshRequired || !ownerActive}
              icon={result.closes ? <CircleSlash className="h-4 w-4" /> : <Check className="h-4 w-4" />}
            >
              {busy ? 'Saving…' : result.repeats ? 'Save feedback and plan another medical' : result.closes ? data.stop === 'declined' ? 'Save as declined' : 'Save and close order' : def.no === STAGES.length ? 'Save and mark Deployed' : 'Save and move'}
            </Button>}
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
    case 'time': {
      const [hour = '', minute = '00'] = String(value ?? '').split(':')
      return <Field label="Interview time (IST)">
        <div className="flex gap-2">
          <Select aria-label="Interview hour (IST)" value={hour} disabled={disabled}
            onChange={event => onChange(event.target.value ? `${event.target.value}:${minute}` : undefined)}>
            <option value="">Hour</option>
            {Array.from({ length: 24 }, (_, h) => <option key={h} value={String(h).padStart(2, '0')}>{interviewHourLabel(h)}</option>)}
          </Select>
          <Select aria-label="Interview minute" value={hour ? minute : ''} disabled={disabled || !hour}
            onChange={event => onChange(`${hour}:${event.target.value}`)}>
            <option value="" disabled>Minute</option>
            {Array.from({ length: 60 }, (_, m) => String(m).padStart(2, '0')).map(m => <option key={m}>{m}</option>)}
          </Select>
        </div>
      </Field>
    }
    case 'radio':
      return (
        <Field label={field.label} required={field.required}>
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
        <Field label={field.label} required={field.required}>
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
              {field.optionsSource === 'users' ? 'Choose a recruiter' : 'Choose one'}
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
        <Field label={field.label} required={field.required}>
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
        <Field label={field.label} required={field.required}>
          <DatePicker value={(value as string) ?? ''} onChange={onChange} disabled={disabled} />
        </Field>
      )
    default:
      return (
        <Field label={field.label} required={field.required}>
          <Input
            type={field.type === 'number' ? 'number' : 'text'}
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

function ClosedRow({ app, canReopen = false, disabled = false, onRecorded, onDone }: { app: Application; canReopen?: boolean; disabled?: boolean; onRecorded?: () => void; onDone?: () => void }) {
  const tab = closureTab(app.closureReason)
  const stageTitle = stageByNo(Math.max(1, app.currentStage))?.title ?? 'Prescreening'
  return <section className="sm-card sm-end is-closed">
    <span className="sm-end-icon" aria-hidden="true"><CircleSlash className="h-5 w-5" /></span>
    <div>
      <b>Closed: {app.closureReason ? closureLabels[app.closureReason] : 'Closed'}</b>
      <p>{tab === null
        ? 'The candidate was removed from this order (or moved to another job) and its open tasks were cancelled. They are on the Tasks list again and can be matched to another order.'
        : tab === 'Declined'
          ? 'The candidate is on the Declined tab of the Tasks page and in All, and can be offered other orders.'
          : 'The candidate is on the Rejected tab of the Tasks page and in All, and can be matched to another order.'} Finished stages and files are above.{canReopen ? ` Reopen ${stageTitle} to continue.` : ''} Admins and MDs can reopen a finished stage by giving a reason.</p>
      {canReopen && <div className="mt-3"><ReopenApplication app={app} stageTitle={stageTitle} disabled={disabled} onRecorded={onRecorded} onDone={onDone} /></div>}
    </div>
  </section>
}
