import { useMemo, useState } from 'react'
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
import { useStore } from '@/shared/state/crm-store'
import { DatePicker } from '@/shared/ui/date-picker'
import { api } from '@/shared/api/client'
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
import type { Application, StageRecord } from '@/shared/model/types'
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
import { FileDrop, FileLink, type StoredFile } from '@/shared/ui/file-drop'

/**
 * The stage editor. Opens centred, with the eleven stages as a horizontal strip you
 * move along as the candidate progresses. Completed stages stay readable but locked;
 * stages beyond the current one are not reachable.
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
  const [viewing, setViewing] = useState<number | null>(null)

  if (!app) return null
  const cand = store.candidate(app.candidateId)!
  const order = store.order(app.orderId)!
  const client = store.company(order.companyId)!
  const designation = store.designation(order.designationId)!
  const owner = store.user(cand.ownerId)!

  const canWork = store.can('work_any_candidate') || cand.ownerId === store.me.id
  const done = new Map(app.stages.map((r) => [r.stage, r]))
  const live = app.status === 'active' ? app.currentStage : null

  // Default to the stage being worked; a completed tab can be opened to read it back.
  const active = viewing ?? live ?? app.stages[app.stages.length - 1]?.stage ?? 1
  const activeRecord = done.get(active)

  return (
    <Dialog
      open={open}
      onClose={onClose}
      width="full"
      title={cand.name}
      description={`${designation.title} · ${client.name} · ${order.ref}`}
    >
      <div className="-mx-6 -my-5">
        <CandidateDetails app={app} />

        {!canWork && (
          <div className="flex items-center gap-2 border-b border-amber-100 bg-amber-50 px-6 py-3 text-xs text-amber-700">
            <Lock className="h-3.5 w-3.5 shrink-0" />
            This candidate belongs to {owner.name}. You can read the record but not record
            stages.
          </div>
        )}

        {/* The eleven stages, left to right */}
        <nav
          aria-label="Stages"
          className="flex flex-wrap gap-1 border-b border-rule-soft bg-field/30 px-6 py-3"
        >
          {STAGES.map((def) => {
            const isDone = done.has(def.no)
            const isLive = live === def.no
            const reachable = isDone || isLive
            const on = active === def.no
            return (
              <button
                key={def.no}
                disabled={!reachable}
                onClick={() => setViewing(def.no)}
                title={reachable ? def.title : 'Complete the earlier stages first'}
                className={cn(
                  'flex shrink-0 items-center gap-2 rounded-control px-3 py-2 text-xs font-medium transition-colors',
                  on
                    ? 'bg-navy-600 text-white'
                    : reachable
                      ? 'text-ink-600 hover:bg-white hover:text-ink'
                      : 'cursor-not-allowed text-ink-300',
                )}
              >
                <span
                  className={cn(
                    'flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] tnum',
                    on
                      ? 'bg-white/25 text-white'
                      : isDone
                        ? 'bg-navy-100 text-navy-700'
                        : isLive
                          ? 'bg-navy-600 text-white'
                          : 'bg-field text-ink-300',
                  )}
                >
                  {isDone && !on ? <Check className="h-3 w-3" /> : def.no}
                </span>
                <span className="whitespace-nowrap">{def.title}</span>
              </button>
            )
          })}
        </nav>

        <div className="px-6 py-5">
          {activeRecord && active !== live ? (
            <CompletedStage record={activeRecord} />
          ) : app.status === 'active' ? (
            <CurrentStage
              key={`${app.currentStage}-${app.stages.length}`}
              app={app}
              canWork={canWork}
              onClosed={onClose}
            />
          ) : app.status === 'deployed' ? (
            <DeployedRow />
          ) : (
            <ClosedRow app={app} />
          )}
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
            {cand.documents.map((d) => {
              const latest = d.versions[d.versions.length - 1]
              return (
                <Fact
                  key={d.key}
                  label={d.label}
                  value={
                    latest ? (
                      <FileLink url={latest.url} title={`Open ${latest.name}`}>
                        {latest.name}
                        {d.versions.length > 1 ? ` · v${d.versions.length}` : ''}
                      </FileLink>
                    ) : (
                      'Not uploaded'
                    )
                  }
                />
              )
            })}
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

function CompletedStage({ record }: { record: StageRecord }) {
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
            Completed by {by?.name} on {dateTimeLabel(record.completedAt)}
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
                  <FileLink url={(record.data[f.name] as StoredFile).url!}>
                    {(record.data[f.name] as StoredFile).name ?? 'Open file'}
                  </FileLink>
                ) : (
                  readValue(f, record.data[f.name], record.data)
                )}
              </dd>
            </div>
          ))}
      </dl>

      <p className="mt-3 text-micro text-ink-400">
        Completed stages are read-only. Only an admin can reopen one, and only from a closed
        application.
      </p>
    </section>
  )
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
  onClosed,
}: {
  app: Application
  canWork: boolean
  onClosed: () => void
}) {
  const store = useStore()
  const { addRemark, saveDraft, completeStage } = store
  const def = stageByNo(app.currentStage)

  // Edits are held locally until Save. Nothing is written as you type, so the form has
  // a dirty state and leaving with unsaved work is a deliberate act, not an accident.
  const [data, setData] = useState<Record<string, unknown>>(app.draft ?? {})
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const result = useMemo(() => def.evaluate(data), [def, data])
  const [attempted, setAttempted] = useState(false)

  const task = store.tasks.find(
    (t) => t.applicationId === app.id && t.stage === app.currentStage && !t.completedAt && !t.cancelledAt,
  )
  const remarks = task ? store.remarksFor(task.id) : []
  const [remark, setRemark] = useState('')

  const set = (name: string, value: unknown) => {
    setData((d) => ({ ...d, [name]: value }))
    setDirty(true)
  }

  const save = async () => {
    setBusy(true)
    try {
      await saveDraft(app.id, data, app.lockVersion ?? 0)
      setDirty(false)
    } catch {
      // The store has already surfaced the reason; a conflict keeps the dirty flag so
      // the work in the form is not thrown away.
    } finally {
      setBusy(false)
    }
  }

  const complete = async () => {
    setAttempted(true)
    if (!result.canComplete) return
    setBusy(true)
    try {
      // Save first, so completion always commits exactly what is on screen.
      if (dirty) await saveDraft(app.id, data, app.lockVersion ?? 0)
      await completeStage(app.id, (app.lockVersion ?? 0) + (dirty ? 1 : 0))
      setDirty(false)
      if (result.closes) onClosed()
    } catch {
      /* reported by the store */
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>

      <div className="rounded-panel border border-ink-300 bg-white">
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-rule-soft px-4 py-3">
          <div className="min-w-0">
            <h3 className="text-h3 font-semibold text-ink">{def.title}</h3>
            <p className="mt-0.5 max-w-[60ch] text-xs text-ink-500">{def.summary}</p>
          </div>
          {task && (
            <div className="w-40 shrink-0">
              <TimeBar createdAt={task.createdAt} />
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
                disabled={!canWork}
                applicationId={app.id}
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
          <div className="flex items-center gap-2">
            {dirty && <span className="text-micro text-amber-700">Unsaved changes</span>}
            <Button variant="secondary" onClick={save} disabled={!canWork || !dirty || busy}>
              {busy ? 'Saving…' : 'Save'}
            </Button>
          <Button
            variant={result.closes ? 'danger' : 'primary'}
            onClick={complete}
            disabled={!canWork || busy}
            icon={result.closes ? <CircleSlash className="h-4 w-4" /> : <Check className="h-4 w-4" />}
          >
            {result.closes ? `Complete and close — ${result.closes.label}` : 'Complete stage'}
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
                disabled={!canWork}
              />
              <Button
                variant="secondary"
                onClick={() => {
                  if (!remark.trim()) return
                  void addRemark(task.id, remark.trim())
                  setRemark('')
                }}
                disabled={!canWork || !remark.trim()}
                icon={<MessageSquarePlus className="h-4 w-4" />}
                className="shrink-0 self-start"
              >
                Add
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
}: {
  field: StageField
  value: unknown
  onChange: (v: unknown) => void
  disabled?: boolean
  applicationId?: string
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
          onChange={onChange}
          busy={uploading}
          disabled={disabled}
          onFile={async (f) => {
            if (!applicationId) return
            setUploading(true)
            try {
              const up = await api.upload('application', applicationId, field.name, f)
              // Keep the identifier in the draft, not the bytes, so saving the stage
              // never re-sends the file.
              onChange({ name: up.name, sizeKb: up.sizeKb, id: up.id, url: up.url, version: up.version })
            } catch (err) {
              store.notify((err as Error).message || 'Upload failed', 'warn')
              onChange(null)
            } finally {
              setUploading(false)
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
  const store = useStore()
  const [confirming, setConfirming] = useState(false)
  const [reason, setReason] = useState('')
  const lastStage = app.stages[app.stages.length - 1]?.stage ?? app.currentStage

  return (
    <section>
      <div className="rounded-panel border border-stamp-100 bg-stamp-50 px-4 py-3">
        <p className="text-sm font-semibold text-stamp-700">
          Application closed — {app.closureReason ? closureLabels[app.closureReason] : 'Closed'}
        </p>
        <p className="mt-0.5 text-xs text-stamp-700/80">
          The candidate is unmapped and back in the Data Bank, ready to be offered another order.
        </p>

        {store.can('reopen_stage') && (
          <div className="mt-3 border-t border-stamp-100 pt-3">
            {confirming ? (
              <div className="space-y-2">
                <Field label="Reason for reopening" required>
                  <Input
                    value={reason}
                    autoFocus
                    placeholder="Why is this being reopened?"
                    onChange={(e) => setReason(e.target.value)}
                  />
                </Field>
                <div className="flex gap-2">
                  <Button
                    variant="primary"
                    size="sm"
                    disabled={!reason.trim()}
                    onClick={() =>
    store.notify('Reopening a stage is not available yet', 'warn')
                    }
                  >
                    Reopen stage {lastStage}
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => setConfirming(false)}>
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setConfirming(true)}
                icon={<RotateCcw className="h-3.5 w-3.5" />}
              >
                Reopen stage {lastStage}
              </Button>
            )}
          </div>
        )}
      </div>
    </section>
  )
}
