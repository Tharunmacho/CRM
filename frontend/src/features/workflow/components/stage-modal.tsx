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
import { DocumentSlotRow, FileDrop, type StoredFile } from '@/shared/ui/file-drop'
import { useWorkspaceReady } from '@/features/master-data/use-workspace-ready'
import { PersonAvatar } from './person-avatar'
import { CertificateSlotRow, MULTI_DOCUMENT_KEYS } from '@/features/candidates/components/certificate-slot-row'
import { SlaMeter, StageRail } from './stage-rail'
import '@/styles/candidate-v2.css'

/**
 * One scrolling application: completed sections are collapsed, the current stage
 * is editable, and later stages stay hidden until the workflow advances.
 */
export function StageModal({
  applicationId,
  open,
  onClose,
  focusStage,
}: {
  applicationId: string | null
  open: boolean
  onClose: () => void
  /** Open this stage's section (expanded) and scroll to it, e.g. when a task for that stage was clicked. */
  focusStage?: number
}) {
  const store = useStore()
  const snapshot = useWorkspaceReady(open && applicationId ? `stage:${applicationId}` : null)
  useEffect(() => {
    if (!open || !focusStage || !snapshot.ready) return
    const timer = window.setTimeout(() => {
      const section = document.querySelector<HTMLElement>(`[data-sm-stage="${focusStage}"]`)
      if (!section) return
      section.querySelector('details')?.setAttribute('open', '')
      section.scrollIntoView({ block: 'start', behavior: 'smooth' })
    }, 80)
    return () => window.clearTimeout(timer)
  }, [open, focusStage, snapshot.ready, applicationId])
  const app = store.applications.find((a) => a.id === applicationId)
  const [files, setFiles] = useState<ApplicationFile[]>([])
  const [fileError, setFileError] = useState(false)
  const [fileRetry, setFileRetry] = useState(0)
  const [dirty, setDirty] = useState(false)
  const [pending, setPending] = useState(false)
  const [reopening, setReopening] = useState(false)

  useEffect(() => {
    if (!open || !applicationId) return
    let live = true
    setFiles([])
    setFileError(false)
    api.get<ApplicationFile[]>(`/files?entity_type=application&entity_id=${encodeURIComponent(applicationId)}`)
      .then((rows) => { if (live) setFiles((current) => [...rows, ...current.filter((file) => !rows.some((row) => row.id === file.id))]) })
      .catch(() => { if (live) setFileError(true) })
    return () => { live = false }
  }, [applicationId, open, fileRetry])

  const cand = app ? store.candidate(app.candidateId) : undefined
  const order = app ? store.order(app.orderId) : undefined
  const client = order ? store.company(order.companyId) : undefined
  const designation = order ? store.designation(order.designationId) : undefined
  if (!snapshot.ready || !app || !cand || !order || !client || !designation) return <Dialog open={open} onClose={onClose} title={cand?.name ?? 'Stages for this order'} description={cand ? 'Stages for this order' : undefined}><p role={snapshot.error ? 'alert' : 'status'} className="text-sm text-ink-600">{snapshot.error || (!snapshot.ready ? 'Loading…' : 'We could not find this order for the candidate any more. Refresh and try again.')}</p>{(snapshot.error || snapshot.ready) && <Button className="mt-4" onClick={snapshot.retry}>Refresh</Button>}</Dialog>
  const owner = store.user(cand.ownerId)

  const canWork = store.can('work_any_candidate') || cand.ownerId === store.me.id
  const effective = app.stages.filter((record) => !record.reopenedReason)
  const superseded = app.stages.filter((record) => record.reopenedReason)
  const live = app.status === 'active' ? app.currentStage : null
  const doneStages = STAGES.filter((definition) => effective.some((record) => record.stage === definition.no) && definition.no !== live)
  const recorded = new Set(effective.map((record) => record.stage)).size

  return (
    <Dialog
      open={open}
      onClose={() => { if (!pending && !reopening && (!dirty || window.confirm('Throw away changes you have not saved?'))) onClose() }}
      width="full"
      title={cand.name}
      description={`${designation.title} · ${client.name} · ${order.ref}`}
    >
      <div className="sm">
        <div className="sm-wrap">
          <ApplicationHeader app={app} recorded={recorded} effective={effective} />

          {!owner?.active && <p role="status" className="sm-note"><AlertTriangle className="h-4 w-4" aria-hidden="true" />Give this candidate an active recruiter (on their profile) before you finish or reopen a stage. You can still save drafts.</p>}
          {!canWork && (
            <p className="sm-note">
              <Lock className="h-4 w-4" aria-hidden="true" />
              {owner ? `${owner.name} looks after this candidate` : 'This candidate has no recruiter yet'}. You can read the stages, but you cannot fill them in.
            </p>
          )}

          <ol className="sm-timeline" aria-label="Stages so far">
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
                    <ReopenStage app={app} stage={definition.no} disabled={pending || reopening || !owner?.active} onBusyChange={setReopening} beforeReopen={() => !dirty || window.confirm('Reopening replaces the stage you are on and throws away changes you have not saved. Continue?')} />
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
                />
              </li>
            ) : app.status === 'deployed' ? (
              <li className="sm-step is-deployed"><span className="sm-node" aria-hidden="true"><Trophy className="h-4 w-4" /></span><DeployedRow /></li>
            ) : (
              <li className="sm-step is-closed"><span className="sm-node" aria-hidden="true"><CircleSlash className="h-4 w-4" /></span><ClosedRow app={app} /></li>
            )}
          </ol>

          {superseded.length > 0 && <details className="sm-extra">
            <summary>Earlier tries kept after reopening ({superseded.length})</summary>
            <p className="sm-hint">These earlier tries stay on record. They do not count towards the progress now.</p>
            <div className="mt-4 space-y-6">{superseded.map((record) => <div key={`${record.stage}-${record.attempt}`}>
              <p className="mb-2 text-xs text-amber-700">Reopened: {record.reopenedReason}</p>
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
  const stageText = app.status === 'deployed' ? 'Deployed' : app.status === 'closed' ? `Closed at stage ${app.currentStage}` : `${app.currentStage}. ${stageByNo(app.currentStage).title}`
  const facts: [string, React.ReactNode, React.ReactNode, boolean?][] = [
    ['Phone', <Phone key="i" className="h-3.5 w-3.5" aria-hidden="true" />, cand.phone, true],
    ['Passport', <BookUser key="i" className="h-3.5 w-3.5" aria-hidden="true" />, cand.passportNo ?? 'Not saved', !!cand.passportNo],
    ['Client', <Building2 key="i" className="h-3.5 w-3.5" aria-hidden="true" />, client.name],
    ['Order', <FileText key="i" className="h-3.5 w-3.5" aria-hidden="true" />, order.ref, true],
    ['Job title', <Briefcase key="i" className="h-3.5 w-3.5" aria-hidden="true" />, designation.title],
    ['Recruiter', <User key="i" className="h-3.5 w-3.5" aria-hidden="true" />, owner?.name ?? 'Not assigned'],
    ['Current stage', <ListChecks key="i" className="h-3.5 w-3.5" aria-hidden="true" />, stageText],
    ['Status', <ShieldCheck key="i" className="h-3.5 w-3.5" aria-hidden="true" />, app.status === 'closed' && app.closureReason ? `Closed: ${closureLabels[app.closureReason]}` : app.status === 'deployed' ? 'Deployed' : 'Active'],
  ]
  return (
    <>
      <header className="sm-head">
        <div className="sm-head-top">
          <div className="sm-person">
            <PersonAvatar name={cand.name} photoUrl={photo?.url} size={56} />
            <div className="min-w-0">
              <p className="sm-eyebrow">Candidate · <span className="tnum">{cand.ref}</span></p>
              <p className="sm-person-role" title={`${designation.title} · ${client.name}`}>{designation.title} · {client.name}</p>
            </div>
          </div>
          <div className="sm-head-side">
            <Badge tone={app.status === 'closed' ? 'stamp' : app.status === 'deployed' ? 'green' : 'navy'} variant="dot">{app.status === 'active' ? 'Active' : app.status === 'deployed' ? 'Deployed' : 'Closed'}</Badge>
            <Badge className="tnum">{recorded} of {STAGES.length} stages done</Badge>
          </div>
        </div>
        <dl className="sm-facts">
          {facts.map(([label, icon, value, ident]) => <div key={label}><dt>{icon}{label}</dt><dd className={cn(ident && 'ident')} title={typeof value === 'string' ? value : undefined}>{value}</dd></div>)}
        </dl>
        <StageRail status={app.status} currentStage={app.currentStage} reached={[...new Set(effective.map(item => item.stage))]} numbered />
        <p className="sm-progress-label">{app.status === 'active' ? `Stage ${app.currentStage} of ${STAGES.length}: ${stageByNo(app.currentStage).title}. Finish it below to open the next stage.` : app.status === 'deployed' ? 'All 11 stages are done.' : 'This order is closed for this candidate. Saved results and documents are below.'}</p>
      </header>
      <button type="button" className="sm-record-toggle" aria-expanded={record} onClick={() => setRecord((value) => !value)}>
        <User className="h-4 w-4" aria-hidden="true" />{record ? 'Hide full candidate details' : 'See full candidate details'}<ChevronDown className="h-4 w-4" aria-hidden="true" />
      </button>
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
        <Fact label="Expected salary" value={money(cand.expectedSalary, 'AED')} />
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
          {cand.documents.map((d) => MULTI_DOCUMENT_KEYS.includes(d.key)
            ? <CertificateSlotRow key={d.key} label={d.label} files={d.versions} uploaderName={(id) => store.user(id)?.name || 'Former team member'} />
            : <DocumentSlotRow key={d.key} label={d.label} versions={d.versions} uploaderName={(id) => store.user(id)?.name || 'Former team member'} />)}
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

      <p className="sm-hint">Saved attempts cannot be changed. If an admin reopens the stage, these details and files stay on record.</p>
    </section>
  )
}

function ReopenStage({ app, stage, disabled, onBusyChange, beforeReopen }: { app: Application; stage: number; disabled: boolean; onBusyChange: (busy: boolean) => void; beforeReopen: () => boolean }) {
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
      try { await store.refresh({ keepCurrent: true }) }
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
      ? money(raw as number, (row.amountCurrency as string) || 'INR')
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
}: {
  app: Application
  canWork: boolean
  onDirtyChange: (dirty: boolean) => void
  onBusyChange: (busy: boolean) => void
  files: ApplicationFile[]
  onUploaded: (file: ApplicationFile) => void
}) {
  const store = useStore()
  const { addRemark } = store
  const def = stageByNo(app.currentStage)
  const ownerActive = !!store.user(store.candidate(app.candidateId)?.ownerId ?? '')?.active

  // Edits are held locally until Save. Nothing is written as you type, so the form has
  // a dirty state and leaving with unsaved work is a deliberate act, not an accident.
  const candidate = store.candidate(app.candidateId)
  const profilePassport = newestFileVersions(candidate?.documents.find((document) => document.key === 'passport')?.versions ?? [])[0]
  // Stage 7 starts from the passport copy already on the profile; the recruiter may replace it.
  const withPassportCopy = (draft: Record<string, unknown>) => app.currentStage === 7 && !draft.copy && profilePassport
    ? { ...draft, copy: { id: profilePassport.id, name: profilePassport.name, sizeKb: profilePassport.sizeKb, url: profilePassport.url, version: profilePassport.version } }
    : draft
  const [data, setData] = useState<Record<string, unknown>>(() => withPassportCopy(app.draft ?? {}))
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
    if (!dirty) { setData(withPassportCopy(app.draft ?? {})); setLockVersion(app.lockVersion ?? 0) }
  }, [app.lockVersion, app.currentStage])

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
    try { await store.refresh({ keepCurrent: true }); setRefreshRequired(false); setError('') }
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
    setBusy(true)
    setError(''); setConflict(null)
    try {
      // The server validates and records exactly these values in one transaction.
      await api.post(`/applications/${app.id}/complete`, { data, lock_version: lockVersion })
      if (!store.isCurrentSession()) return
      setDirty(false)
      setCompleted(true)
      store.notify(result.repeats ? 'Medical result saved. A follow-up task was created.' : result.closes ? 'This order is closed for the candidate. Everything stays on record.' : 'Stage done')
      await refreshAfterWrite()
    } catch (cause) { reportFailure(cause) } finally {
      setBusy(false)
    }
  }

  // Half-width fields sit in pairs; one left without a partner takes the full row so no cell is orphaned.
  const visibleFields = def.fields.filter((f) => fieldVisible(f, data))
  const fieldSpan: string[] = []
  let halfRun = 0
  visibleFields.forEach((f, index) => {
    if (f.width !== 'half') { fieldSpan.push('is-full'); halfRun = 0; return }
    if (halfRun % 2 === 1) { fieldSpan.push('is-half'); halfRun += 1; return }
    if (visibleFields[index + 1]?.width === 'half') { fieldSpan.push('is-half'); halfRun += 1 } else { fieldSpan.push('is-full'); halfRun = 0 }
  })
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
            <p>{def.summary}</p>
          </div>
          {task && (
            <div className="sm-sla">
              <SlaMeter startedAt={taskClockStart(task)} title={task.title} due={dateTimeLabel(task.dueAt)} />
            </div>
          )}
        </header>

        <div className={cn('sm-check', checkState)} aria-live="polite">
          <span className="sm-check-icon" aria-hidden="true">{result.closes ? <AlertTriangle className="h-4 w-4" /> : ready || result.repeats ? <CheckCircle2 className="h-4 w-4" /> : <ListChecks className="h-4 w-4" />}</span>
          <div className="min-w-0">
            <b>{result.canComplete ? 'Ready to save the result' : 'Before you can finish this stage'}</b>
            {result.missing.length > 0
              ? <ul>{result.missing.map(item => <li key={item}>{item}</li>)}</ul>
              : <p>{result.repeats ? 'This saves the medical result and sets up the next medical task.' : result.closes ? `This closes the order for this candidate: ${result.closes.label}. Work done so far stays on record.` : def.no < STAGES.length ? `Next: stage ${def.no + 1}, ${stageByNo(def.no + 1).title}. A new task is created when you finish this stage.` : 'This finishes the deployment and adds one to the order’s filled openings.'}</p>}
          </div>
        </div>

        <div className="sm-form">
          {visibleFields.map((f, index) => (
            <div key={f.name} className={fieldSpan[index]}>
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

        {def.closer && (
          <p className="sm-form-note">
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
            A “no” result here closes this order for the candidate. They go back to the Data Bank and can be matched to another order.
          </p>
        )}

        {attempted && result.missing.length > 0 && (
          <p className="sm-still">
            Still needed: {result.missing.join(', ')}.
          </p>
        )}

        <footer className="sm-foot">
          <span className="sm-foot-meta">
            Save draft keeps what you typed. Complete stage saves the result and moves to the next stage.
            {def.repeatable && ' This stage can be done again.'}
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
              {result.repeats ? 'Save result and plan another medical' : result.closes ? `Finish and close: ${result.closes.label}` : 'Complete stage'}
            </Button>
          </div>
        </footer>
      </section>

      {/* Daily remarks live beside the stage, because that is where staff are working. */}
      {task && (
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
      : (field.options ?? [])

  // Rows written before this field became a picker hold a bare name — match on it so
  // the existing selection still shows.
  const selectedValue =
    field.optionsSource === 'users'
      ? userRefId(value) ||
        (store.users.find((u) => u.name === userRefName(value))?.id ?? '')
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
