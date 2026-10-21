import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  BookUser,
  Briefcase,
  Building2,
  Check,
  ChevronDown,
  CircleSlash,
  FileText,
  Lock,
  MessageSquarePlus,
  Phone,
  PhoneCall,
  PlaneTakeoff,
  RotateCcw,
  User,
} from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { dateLabel, dateTimeLabel, money, smartTime } from '@/shared/lib/format'
import { applicationFileSlot, applicationFileVersions, type ApplicationFile } from '@/shared/lib/document-files'
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
import { Badge, StatusPill, TimeBar } from '@/shared/ui/display'
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

/**
 * One scrolling application: completed sections are collapsed, the current stage
 * is editable, and later stages stay hidden until the workflow advances.
 */
export function StageModal({
  applicationId,
  open,
  onClose,
}: {
  applicationId: string | null
  open: boolean
  onClose: () => void
}) {
  const store = useStore()
  const app = store.applications.find((a) => a.id === applicationId)
  const [files, setFiles] = useState<ApplicationFile[]>([])
  const [fileError, setFileError] = useState(false)
  const [fileRetry, setFileRetry] = useState(0)
  const [dirty, setDirty] = useState(false)
  const [pending, setPending] = useState(false)

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

  if (!app) return null
  const cand = store.candidate(app.candidateId)!
  const order = store.order(app.orderId)!
  const client = store.company(order.companyId)!
  const designation = store.designation(order.designationId)!
  const owner = store.user(cand.ownerId)

  const canWork = store.can('work_any_candidate') || cand.ownerId === store.me.id
  const effective = app.stages.filter((record) => !record.reopenedReason)
  const superseded = app.stages.filter((record) => record.reopenedReason)
  const live = app.status === 'active' ? app.currentStage : null

  return (
    <Dialog
      open={open}
      onClose={() => { if (!pending && (!dirty || window.confirm('Discard unsaved stage changes?'))) onClose() }}
      width="full"
      title={cand.name}
      description={`${designation.title} · ${client.name} · ${order.ref}`}
    >
      <div className="-mx-6 -my-5">
        <CandidateDetails app={app} />

        {!canWork && (
          <div className="flex items-center gap-2 border-b border-amber-100 bg-amber-50 px-6 py-3 text-xs text-amber-700">
            <Lock className="h-3.5 w-3.5 shrink-0" />
            This candidate belongs to {owner?.name ?? 'an unassigned recruiter'}. You can read the record but not record
            stages.
          </div>
        )}

        <div className="space-y-4 px-4 py-5 sm:px-6" aria-label="Application stage history">
          {fileError && <p role="alert" className="mb-3 text-xs text-stamp-700">File history could not be loaded. <button type="button" className="underline" onClick={() => setFileRetry((v) => v + 1)}>Retry</button></p>}
          {STAGES.filter((definition) => effective.some((record) => record.stage === definition.no) && definition.no !== live).map((definition) => {
            const records = effective.filter((record) => record.stage === definition.no)
            const latest = records[records.length - 1]
            return <details key={`${definition.no}-${latest.attempt}`} className="group rounded-panel border border-rule bg-white">
              <summary className="flex cursor-pointer list-none flex-wrap items-center gap-3 px-4 py-4">
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-navy-50 text-sm font-semibold text-navy-700">{definition.no}</span>
                <span className="min-w-0 flex-1"><span className="block text-sm font-semibold">{definition.title}</span><span className="text-xs text-ink-500">{store.user(latest.completedBy)?.name ?? 'Former team member'} · {dateTimeLabel(latest.completedAt)}</span></span>
                <Badge tone="navy">{latest.outcome}</Badge>
                <ChevronDown className="h-4 w-4 text-ink-400 transition-transform group-open:rotate-180" />
              </summary>
              <div className="space-y-5 border-t border-rule px-4 py-4">{records.map((record) => <CompletedStage key={`${record.stage}-${record.attempt}`} record={record} files={files} app={app} />)}
                <ReopenStage app={app} stage={definition.no} />
              </div>
            </details>
          })}
          {live === 6 && effective.some((record) => record.stage === 6) && <details className="rounded-panel border border-amber-200 bg-amber-50/40 p-4">
            <summary className="cursor-pointer text-sm font-semibold">Previous medical attempts ({effective.filter((record) => record.stage === 6).length})</summary>
            <div className="mt-4 space-y-5">{effective.filter((record) => record.stage === 6).map((record) => <CompletedStage key={record.attempt} record={record} files={files} app={app} />)}</div>
          </details>}
          {app.status === 'active' ? (
            <CurrentStage
              key={`${app.id}-${app.currentStage}-${app.stages.length}`}
              app={app}
              canWork={canWork}
              onDirtyChange={setDirty}
              onBusyChange={setPending}
              files={files}
              onUploaded={(file) => setFiles((current) => [...current.filter((item) => item.id !== file.id), file])}
            />
          ) : app.status === 'deployed' ? (
            <DeployedRow />
          ) : (
            <ClosedRow app={app} />
          )}
          {superseded.length > 0 && <details className="rounded-panel border border-rule bg-field/30 p-4">
            <summary className="cursor-pointer text-sm font-medium text-ink-600">History retained after reopening ({superseded.length} attempts)</summary>
            <p className="mt-2 text-xs text-ink-500">These earlier attempts remain on record. They are excluded from the current application progress.</p>
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

/**
 * The full candidate record, in segments. Collapsed by default so the stage work
 * stays the first thing you see; opening it reveals everything on file.
 */
function CandidateDetails({ app }: { app: Application }) {
  const store = useStore()
  const [open, setOpen] = useState(false)

  const cand = store.candidate(app.candidateId)!
  const order = store.order(app.orderId)!
  const client = store.company(order.companyId)!
  const designation = store.designation(order.designationId)!
  const owner = store.user(cand.ownerId)

  return (
    <section className="border-b border-rule-soft">
      <div className="flex flex-wrap items-center gap-4 px-6 py-4">
        <dl className="grid min-w-0 flex-1 grid-cols-2 gap-x-8 gap-y-3 sm:grid-cols-4">
          <Cell label="Phone" value={cand.phone} ident />
          <Cell label="Passport" value={cand.passportNo ?? 'Not on file'} ident={!!cand.passportNo} />
          <Cell label="Owner" value={owner?.name ?? '—'} />
          <Cell
            label="Stage"
            value={
              app.status === 'deployed'
                ? 'Deployed'
                : app.status === 'closed'
                  ? 'Closed'
                  : `${app.currentStage} of ${STAGES.length}`
            }
          />
        </dl>
        <div className="flex shrink-0 items-center gap-3">
          <div className="flex flex-col items-end gap-1.5">
            <StatusPill status={app.status} />
            {app.status === 'closed' && app.closureReason && (
              <Badge tone="stamp">{closureLabels[app.closureReason]}</Badge>
            )}
          </div>
          <button
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="flex items-center gap-1.5 rounded-control border border-rule px-3 py-2 text-xs font-medium text-ink-600 transition-colors hover:border-ink-300 hover:text-ink"
          >
            {open ? 'Hide details' : 'Details'}
            <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', open && 'rotate-180')} />
          </button>
        </div>
      </div>

      {open && (
        <div className="space-y-7 border-t border-rule-soft bg-field/20 px-6 py-6">
          <Segment
            icon={<User className="h-4 w-4" />}
            title="Personal Information"
            subtitle="Basic candidate details and background"
          >
            <Fact label="Name" value={cand.name} />
            <Fact label="Reference" value={cand.ref} ident />
            <Fact label="Date of Birth" value={dateLabel(cand.dob)} />
            <Fact label="Gender" value={cand.gender ?? 'Not provided'} className="capitalize" />
            <Fact label="Nationality" value={cand.nationality} />
            <Fact label="Current Location" value={cand.currentCity || 'Not provided'} />
            <Fact label="Languages Known" value={cand.languages.join(', ') || 'Not provided'} />
            <Fact label="Date Added" value={dateLabel(cand.addedAt)} />
          </Segment>

          <Segment
            icon={<Phone className="h-4 w-4" />}
            title="Contact"
            subtitle="How to reach this candidate"
          >
            <Fact label="Mobile" value={cand.phone} ident />
            <Fact label="WhatsApp" value={cand.whatsapp ?? 'Not provided'} ident={!!cand.whatsapp} />
            <Fact label="Email" value={cand.email ?? 'Not provided'} />
            <Fact
              label="Source"
              value={
                cand.source.replace('_', '-') +
                (cand.sourceDetail ? ` · ${cand.sourceDetail}` : '')
              }
              className="capitalize"
            />
          </Segment>

          <Segment
            icon={<BookUser className="h-4 w-4" />}
            title="Passport"
            subtitle="Required before the original is collected at stage 7"
          >
            <Fact label="Passport Number" value={cand.passportNo ?? 'Not on file'} ident={!!cand.passportNo} />
            <Fact label="Place of Issue" value={cand.passportPlace ?? 'Not provided'} />
            <Fact label="Issue Date" value={dateLabel(cand.passportIssue)} />
            <Fact label="Expiry Date" value={dateLabel(cand.passportExpiry)} />
          </Segment>

          <Segment
            icon={<Briefcase className="h-4 w-4" />}
            title="Work Profile"
            subtitle="Trade, experience and expectations"
          >
            <Fact label="Preferred Designation" value={cand.designations.join(', ') || 'Not provided'} />
            <Fact label="Total Experience" value={`${cand.totalExperience} years`} />
            <Fact
              label="Gulf / Overseas Experience"
              value={cand.gulfExperience ? `${cand.gulfExperience} years` : 'None'}
            />
            <Fact label="Expected Salary" value={money(cand.expectedSalary, 'AED')} />
            <div className="sm:col-span-4">
              <p className="mb-1.5 text-xs font-semibold text-ink">Skills</p>
              <div className="flex flex-wrap gap-1.5">
                {cand.skills.length ? (
                  cand.skills.map((sk) => <Badge key={sk}>{sk}</Badge>)
                ) : (
                  <span className="text-sm text-ink-400">Not provided</span>
                )}
              </div>
            </div>
          </Segment>

          <Segment
            icon={<Building2 className="h-4 w-4" />}
            title="Order"
            subtitle="The opening this candidate is mapped to"
          >
            <Fact label="Client" value={client.name} />
            <Fact label="Designation" value={designation.title} />
            <Fact label="Order Reference" value={order.ref} ident />
            <Fact label="Location" value={`${order.city}, ${order.country}`} />
            <Fact label="Salary" value={money(order.salary, order.currency)} />
            <Fact label="Contract Period" value={order.contractPeriod} />
            <Fact label="Interview Date" value={dateLabel(order.interviewDate)} />
            <Fact label="Order Deadline" value={dateLabel(order.deadline)} />
          </Segment>

          {cand.callOutcome && (
            <Segment
              icon={<PhoneCall className="h-4 w-4" />}
              title="Initial Call"
              subtitle="Outcome of the first call with the candidate"
            >
              <Fact
                label="Interested"
                value={
                  cand.callOutcome.interested === 'yes'
                    ? 'Yes'
                    : cand.callOutcome.interested === 'no'
                      ? 'No'
                      : 'Not reachable'
                }
              />
              <Fact label="Recorded" value={dateTimeLabel(cand.callOutcome.recordedAt)} />
              <div className="sm:col-span-2">
                <p className="mb-1.5 text-xs font-semibold text-ink">Remarks</p>
                <p className="rounded-control border border-rule-soft bg-white px-3 py-2.5 text-sm text-ink">
                  {cand.callOutcome.remarks || 'Not provided'}
                </p>
              </div>
            </Segment>
          )}

          <Segment
            icon={<FileText className="h-4 w-4" />}
            title="Documents"
            subtitle="Nothing is overwritten — replacing a file adds a version"
          >
            <div className="sm:col-span-2 lg:col-span-4">
              {cand.documents.map((d) => <DocumentSlotRow key={d.key} label={d.label} versions={d.versions} uploaderName={(id) => store.user(id)?.name || 'Former team member'} />)}
            </div>
          </Segment>
        </div>
      )}
    </section>
  )
}

function Segment({
  icon,
  title,
  subtitle,
  children,
}: {
  icon: React.ReactNode
  title: string
  subtitle: string
  children: React.ReactNode
}) {
  return (
    <section>
      <header className="border-b border-rule-soft pb-3">
        <h4 className="flex items-center gap-2 text-sm font-semibold text-ink">
          <span className="text-ink-400">{icon}</span>
          {title}
        </h4>
        <p className="mt-0.5 text-xs text-ink-400">{subtitle}</p>
      </header>
      <div className="mt-4 grid gap-x-4 gap-y-4 sm:grid-cols-2 lg:grid-cols-4">{children}</div>
    </section>
  )
}

/** A read-only field, boxed so the record scans like a filled-in form. */
function Fact({
  label,
  value,
  ident,
  className,
}: {
  label: string
  value: React.ReactNode
  ident?: boolean
  className?: string
}) {
  return (
    <div className="min-w-0">
      <p className="mb-1.5 text-xs font-semibold text-ink">{label}</p>
      <p
        className={cn(
          'truncate rounded-control border border-rule-soft bg-white px-3 py-2.5 text-sm text-ink',
          ident && 'ident',
          className,
        )}
        title={typeof value === 'string' ? value : undefined}
      >
        {value}
      </p>
    </div>
  )
}

function Cell({ label, value, ident }: { label: string; value: string; ident?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-micro text-ink-400">{label}</dt>
      <dd className={cn('mt-0.5 truncate text-sm text-ink', ident && 'ident')}>{value}</dd>
    </div>
  )
}

/* -------------------------------------------------------- Completed stage */

function CompletedStage({ record, files, app }: { record: StageRecord; files: ApplicationFile[]; app: Application }) {
  const store = useStore()
  const def = stageByNo(record.stage)
  const by = store.user(record.completedBy)
  const negative = ['Failed', 'Rejected', 'Unfit', 'Not forwarded'].includes(record.outcome)

  return (
    <section>
      <header className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h3 className="text-h3 font-semibold text-ink">{def.title}</h3>
          <p className="mt-0.5 text-xs text-ink-500">
            Attempt {record.attempt} · Recorded by {by?.name ?? 'Former team member'} on {dateTimeLabel(record.completedAt)}
          </p>
        </div>
        <Badge tone={negative ? 'stamp' : 'navy'}>{record.outcome}</Badge>
      </header>

      <dl className="grid gap-x-8 gap-y-4 rounded-control border border-rule-soft bg-field/30 p-5 sm:grid-cols-2">
        {def.fields
          .filter((f) => fieldVisible(f, record.data) && record.data[f.name] != null)
          .map((f) => (
            <div key={f.name} className="min-w-0">
              <dt className="text-micro text-ink-400">{f.label}</dt>
              <dd className={cn('mt-0.5 text-sm text-ink-700', f.ident && 'ident')}>
                {f.type === 'file' && (record.data[f.name] as StoredFile).url ? (
                  <FileDrop value={record.data[f.name] as StoredFile} versions={applicationFileVersions(files, app, record.stage, f.name)} disabled uploaderName={(id) => store.user(id)?.name || 'Former team member'} />
                ) : (
                  readValue(f, record.data[f.name], record.data)
                )}
              </dd>
            </div>
          ))}
      </dl>

      <p className="mt-3 text-micro text-ink-400">Recorded attempts are read-only. Reopening retains their evidence and audit history.</p>
    </section>
  )
}

function ReopenStage({ app, stage }: { app: Application; stage: number }) {
  const store = useStore()
  const [editing, setEditing] = useState(false)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  if (!store.can('reopen_stage')) return null
  const reopen = async () => {
    if (!reason.trim() || busy || saved) return
    setBusy(true); setError('')
    try {
      await api.post(`/applications/${app.id}/reopen`, { stage, reason: reason.trim(), lock_version: app.lockVersion })
      if (!store.isCurrentSession()) return
      setSaved(true)
      store.notify(`Stage ${stage} reopened. Earlier attempts remain in history.`)
      try { await store.refresh({ keepCurrent: true }) }
      catch { setError('Reopening was saved. Refresh the application to load the current stage.') }
    } catch (cause) {
      if (store.isCurrentSession()) setError(cause instanceof Error ? cause.message : 'The stage could not be reopened.')
    } finally { setBusy(false) }
  }
  return <div className="border-t border-rule pt-4">
    {error && <p role="alert" className="mb-3 text-sm text-stamp-700">{error}</p>}
    {saved ? <Button size="sm" onClick={() => void store.refresh({ keepCurrent: true }).catch(() => setError('The application could not refresh. Please try again.'))}>Refresh saved application</Button>
      : editing ? <div className="space-y-3">
        <p className="text-xs text-ink-600">This restarts stage {stage}, supersedes later completed stages and replaces the current open task. Original records and files remain in history. Reopening a deployment also updates the order count.</p>
        <Field label="Reason for reopening" required><Textarea value={reason} onChange={(event) => setReason(event.target.value)} maxLength={2000} disabled={busy} placeholder="Describe the correction needed" /></Field>
        <div className="flex flex-wrap gap-2"><Button variant="primary" disabled={busy || !reason.trim()} onClick={() => void reopen()}>{busy ? 'Reopening…' : `Reopen stage ${stage}`}</Button><Button disabled={busy} onClick={() => { setEditing(false); setError('') }}>Cancel</Button></div>
      </div> : <Button variant="secondary" size="sm" icon={<RotateCcw className="h-4 w-4" />} onClick={() => setEditing(true)}>Reopen stage {stage}</Button>}
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

  // Edits are held locally until Save. Nothing is written as you type, so the form has
  // a dirty state and leaving with unsaved work is a deliberate act, not an accident.
  const [data, setData] = useState<Record<string, unknown>>(app.draft ?? {})
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
  const [remarkBusy, setRemarkBusy] = useState(false)

  useEffect(() => { onDirtyChange(dirty); return () => onDirtyChange(false) }, [dirty, onDirtyChange])
  useEffect(() => {
    onBusyChange(busy || uploading || remarkBusy)
    return () => onBusyChange(false)
  }, [busy, uploading, remarkBusy, onBusyChange])

  useEffect(() => {
    if (!dirty) { setData(app.draft ?? {}); setLockVersion(app.lockVersion ?? 0) }
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
    setError([cause instanceof Error ? cause.message : 'This change could not be saved.', ...(details?.missing ?? [])].join(' '))
    if (cause instanceof ApiError && cause.isConflict) setConflict(details)
  }

  const refreshAfterWrite = async () => {
    try { await store.refresh({ keepCurrent: true }) }
    catch { if (store.isCurrentSession()) setError('Your change was saved. Refresh the application to load the latest history.') }
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
      store.notify('Stage draft saved')
      await refreshAfterWrite()
    } catch (cause) { reportFailure(cause) } finally {
      setBusy(false)
    }
  }

  const complete = async () => {
    if (uploading || busy || completed) return
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
      store.notify(result.repeats ? 'Medical attempt recorded. Follow-up task created.' : result.closes ? 'Application closed; history retained.' : 'Stage completed')
      await refreshAfterWrite()
    } catch (cause) { reportFailure(cause) } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      {error && <div role="alert" className="mb-3 rounded-control border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
        <p>{error}</p>
        {conflict && <div className="mt-3 flex flex-wrap gap-2">
          {conflict.current_stage === app.currentStage && conflict.status === 'active' && <Button size="sm" onClick={() => { setData(conflict.draft ?? {}); setLockVersion(conflict.lock_version ?? lockVersion); setDirty(false); setConflict(null); setError('') }}>Load saved values</Button>}
          <Button size="sm" onClick={() => void store.refresh({ keepCurrent: true }).catch(reportFailure)}>Refresh application</Button>
        </div>}
      </div>}
      {completed && <div role="status" className="mb-3 rounded-control border border-navy-100 bg-navy-50 p-3 text-sm text-navy-800">This result was saved. <button className="underline" onClick={() => void refreshAfterWrite()}>Refresh history</button></div>}
      <div className="rounded-panel border border-ink-300 bg-white">
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-rule-soft px-4 py-3">
          <div className="min-w-0">
            <h3 className="text-h3 font-semibold text-ink">{def.title}</h3>
            <p className="mt-0.5 max-w-[60ch] text-xs text-ink-500">{def.summary}</p>
          </div>
          {task && (
            <div className="w-40 shrink-0">
              <TimeBar createdAt={taskClockStart(task)} />
              <p className="mt-1 text-right text-micro text-ink-400">48-hour limit</p>
            </div>
          )}
        </header>

        <div className="grid gap-4 px-4 py-4 sm:grid-cols-2">
          {def.fields.filter((f) => fieldVisible(f, data)).map((f) => (
            <div key={f.name} className={f.width === 'half' ? 'sm:col-span-1' : 'sm:col-span-2'}>
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
          <p className="flex items-start gap-2 border-t border-rule-soft px-4 py-2.5 text-micro text-ink-400">
            <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0 text-amber-600" />
            A negative outcome here closes the application. The candidate returns to the Data Bank
            and can be mapped to another order.
          </p>
        )}

        {attempted && result.missing.length > 0 && (
          <p className="border-t border-stamp-100 bg-stamp-50 px-4 py-2.5 text-xs text-stamp-700">
            Still needed: {result.missing.join(', ')}.
          </p>
        )}

        <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-rule-soft bg-field/40 px-4 py-3">
          <span className="text-micro text-ink-400">
            Stage {def.no} of {STAGES.length}
            {def.repeatable && ' · can repeat'}
          </span>
          <div className="flex flex-wrap items-center gap-2">
            {uploading ? <span role="status" className="text-micro text-amber-700">Uploading documents…</span> : dirty && <span className="text-micro text-amber-700">Unsaved changes</span>}
            <Button variant="secondary" onClick={save} disabled={!canWork || !dirty || busy || uploading || completed}>
              {busy ? 'Saving…' : 'Save'}
            </Button>
          <Button
            variant={result.closes ? 'danger' : 'primary'}
            onClick={complete}
            disabled={!canWork || busy || uploading || completed}
            icon={result.closes ? <CircleSlash className="h-4 w-4" /> : <Check className="h-4 w-4" />}
          >
            {result.repeats ? 'Record attempt and schedule re-medical' : result.closes ? `Complete and close — ${result.closes.label}` : 'Complete stage'}
          </Button>
          </div>
        </footer>
      </div>

      {/* Daily remarks live beside the stage, because that is where staff are working. */}
      {task && (
        <div className="mt-3 rounded-panel border border-rule bg-white">
          <div className="flex items-center justify-between gap-2 border-b border-rule-soft px-4 py-2.5">
            <h4 className="text-xs font-medium text-ink">Daily updates</h4>
            {!store.updatedToday(task.id) && (
              <Badge tone="amber" dot>
                Not updated today
              </Badge>
            )}
          </div>
          <div className="px-4 py-3">
            <div className="flex gap-2">
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
                className="shrink-0 self-start"
              >
                {remarkBusy ? 'Saving…' : 'Add'}
              </Button>
            </div>

            {remarks.length > 0 && (
              <ul className="mt-3 space-y-2.5 border-t border-rule-soft pt-3">
                {remarks.map((r) => (
                  <li key={r.id} className="flex gap-2.5">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm leading-snug text-ink-700">{r.body}</p>
                      <p className="mt-0.5 text-micro text-ink-400">
                        {store.user(r.authorId)?.name} · {smartTime(r.createdAt)}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </section>
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
        <Field label={field.label} hint={field.help}>
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
        <Field label={field.label} hint={field.help}>
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
        <Field label={field.label} hint={field.help}>
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
          label={field.label}
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
        <Field label={field.label} hint={field.help}>
          <DatePicker value={(value as string) ?? ''} onChange={onChange} disabled={disabled} />
        </Field>
      )
    default:
      return (
        <Field label={field.label} hint={field.help}>
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
    <section>
      <div className="rounded-panel border border-ink-800 bg-ink-800 px-4 py-3 animate-stamp-in">
        <p className="text-sm font-semibold text-white">Deployed</p>
        <p className="mt-0.5 text-xs text-ink-300">
          The order's deployed count went up by one. When deployed equals openings the order moves
          to Filled.
        </p>
      </div>
    </section>
  )
}

function ClosedRow({ app }: { app: Application }) {
  return <section className="rounded-panel border border-stamp-100 bg-stamp-50 px-4 py-3">
    <p className="text-sm font-semibold text-stamp-700">Application closed: {app.closureReason ? closureLabels[app.closureReason] : 'Closed'}</p>
    <p className="mt-1 text-xs text-stamp-700/80">The candidate is back in the Data Bank. Completed stages and files remain available above; an administrator can reopen a completed stage with a reason.</p>
  </section>
}
