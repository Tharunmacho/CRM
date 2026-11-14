import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { roleLabel } from '@/shared/model/role-labels'
import { Select } from '@/shared/ui/primitives'
import { SuggestInput } from '@/shared/ui/suggest-input'
import { SOURCE_OPTIONS, cleanAgencyName, jobOptionText, jobUnavailableReason } from '../form-model'
import { applicationOptions, applicationOptionText } from '../paper-model'
import type { ApplicationRef, AssignmentChange, AssignmentData } from '../types'
import type { AssignmentField } from '../use-application-form'

const NOT_SET = <span className="af-assign-empty">Not set</span>
const NOT_ASSIGNED_TEXT = <span className="af-assign-empty">Not assigned</span>

/** One labelled slot of the block: the label with its saving mark, the control, then the inline error. */
function Field({ id, label, saving, error, hint, children }: { id: string; label: string; saving?: boolean; error?: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className={cn('af-assign-field', error && 'has-error')} data-assign={id}>
      <div className="af-assign-label">
        <label htmlFor={`af-assign-${id}`}>{label}</label>
        {saving && <span className="af-assign-saving" role="status"><Loader2 aria-hidden="true" />Saving</span>}
      </div>
      {children}
      {error ? <p className="af-note is-error" role="alert">{error}</p> : hint ? <p className="af-note">{hint}</p> : null}
    </div>
  )
}

/**
 * What this candidate is for: Client, Job, Source, Agency and Recruiter, always at the top of the form. Each pick is saved at once;
 * the value shows immediately and goes back to the old one, with the server's sentence under the field, when the server refuses.
 * Picking a client only narrows the Job list: the change is saved when a job is chosen, because the job is what implies the client.
 */
export function AssignmentBar({
  assignment,
  saving,
  errors,
  onChange,
  applications = [],
  activeApplicationId = '',
  onSwitchApplication,
  stageControl,
  canManageJobs = false,
}: {
  assignment: AssignmentData
  saving: Partial<Record<AssignmentField, boolean>>
  errors: Partial<Record<AssignmentField, string>>
  onChange: (field: AssignmentField, change: AssignmentChange) => void
  /** The candidate's applications, when there are several: the Job list shows them first, and picking one switches the view to it. */
  applications?: ApplicationRef[]
  activeApplicationId?: string
  onSwitchApplication?: (applicationId: string) => void
  stageControl?: ReactNode
  canManageJobs?: boolean
}) {
  const { options, can } = assignment
  const [clientId, setClientId] = useState(assignment.client?.id ?? '')
  // The client follows the server (a colleague changed the job, or the save was answered); a pick that is not saved yet is kept.
  const serverClientId = assignment.client?.id ?? ''
  useEffect(() => setClientId(serverClientId), [serverClientId])

  const client = options.clients.find((item) => item.id === clientId)
  const editJob = can.job
  const jobs = useMemo(() => {
    const list = [...(client?.jobs ?? [])]
    // The job the candidate is on stays in its list even when it is closed or filled now.
    if (assignment.job?.orderId && client && client.id === serverClientId && !list.some((job) => job.orderId === assignment.job?.orderId)) {
      list.unshift({ orderId: assignment.job.orderId, ref: assignment.job.ref, title: assignment.job.title, openings: null, status: '' })
    }
    return list
  }, [client, assignment.job, serverClientId])
  const jobValue = assignment.job && client && client.id === serverClientId ? assignment.job.orderId : ''
  const noAvailableJobs = Boolean(clientId) && !jobs.some((job) => job.orderId === jobValue || !jobUnavailableReason(job))

  const [agencyText, setAgencyText] = useState(cleanAgencyName(assignment.agency?.name))
  const serverAgency = cleanAgencyName(assignment.agency?.name)
  useEffect(() => setAgencyText(serverAgency), [serverAgency])
  const commitAgency = () => {
    const name = agencyText.trim()
    if (name === serverAgency) return
    const known = options.agencies.find((item) => item.name.toLowerCase() === name.toLowerCase())
    if (known) onChange('agency', { agencyId: known.id })
    else if (name) onChange('agency', { agencyId: null, agencyName: name })
    else onChange('agency', { agencyId: null, agencyName: null })
  }

  // Choosing a known agency from the list saves at once; a name that is typed is saved when the box is left or Enter is pressed.
  const typedKnown = options.agencies.some((item) => item.name.toLowerCase() === agencyText.trim().toLowerCase())
  useEffect(() => { if (typedKnown && agencyText.trim() !== serverAgency) commitAgency() }, [agencyText]) // eslint-disable-line react-hooks/exhaustive-deps

  const sourceKey = assignment.source.key
  const sourceOptions = SOURCE_OPTIONS.some((item) => item.key === sourceKey) || !sourceKey ? SOURCE_OPTIONS : [...SOURCE_OPTIONS, { key: sourceKey, label: assignment.source.label }]

  // With several applications the Job list starts with them (current ones first, the one on screen marked).
  const several = applications.length > 1 && Boolean(onSwitchApplication)
  const owned = several ? applicationOptions(applications, activeApplicationId) : []
  const ownedOrders = new Set(owned.map((item) => item.orderId))
  const shownApplication = owned.find((item) => item.id === activeApplicationId) ?? owned.find((item) => item.orderId === jobValue)
  const jobSelectValue = several && shownApplication ? `app:${shownApplication.id}` : jobValue

  // The jobs a candidate can be put on come first; filled, closed and on-hold jobs follow (greyed out), so the list opens on what is available.
  const pickableJobs = useMemo(() => {
    const rows = jobs.filter((job) => !ownedOrders.has(job.orderId)).map((job, index) => ({
      job, index, reason: job.orderId === jobValue ? '' : jobUnavailableReason(job),
    }))
    return rows.sort((a, b) => Number(Boolean(a.reason)) - Number(Boolean(b.reason)) || a.index - b.index)
  }, [jobs, ownedOrders, jobValue])

  const pickJob = (value: string) => {
    if (value.startsWith('app:')) {
      const id = value.slice(4)
      if (id !== activeApplicationId) onSwitchApplication?.(id)
      return
    }
    const orderId = value
    if (orderId === jobValue) return
    if (!orderId) {
      if (!window.confirm('Remove this candidate from the job? Their progress on it stays in the history.')) return
      onChange('job', { orderId: null })
      return
    }
    onChange('job', { orderId })
  }

  return (
    <section className="af-assign" aria-label="Client, job, source, agency and recruiter">
      <Field id="client" label="Client" error={errors.client}
        hint={editJob && clientId && clientId !== serverClientId ? 'Now choose a job for this client.' : undefined}>
        {editJob ? (
          <Select id="af-assign-client" searchable value={clientId} aria-label="Client" onChange={(event) => setClientId(event.target.value)}>
            <option value="">{assignment.client && !client ? assignment.client.name : 'Select a client'}</option>
            {options.clients.map((item) => <option key={item.id} value={item.id}>{item.companyName && item.companyName !== item.name ? `${item.name} · ${item.companyName}` : item.name}</option>)}
          </Select>
        ) : (
          <p className="af-assign-value" id="af-assign-client" title={assignment.client?.companyName || undefined}>{assignment.client?.name || NOT_SET}</p>
        )}
      </Field>

      <Field id="job" label="Job" saving={saving.job} error={errors.job}
        hint={editJob && noAvailableJobs ? <>{jobs.length ? 'These jobs are filled, closed or on hold. A job must be reopened before assigning another candidate.' : 'No jobs have been added for this client.'}{canManageJobs && <> <Link to="/jobs">Manage jobs</Link></>}</> : !editJob && assignment.job && (assignment.job.ref || assignment.job.country) ? [assignment.job.ref, assignment.job.country].filter(Boolean).join(' · ') : undefined}>
        {editJob ? (
          <Select id="af-assign-job" searchable value={jobSelectValue} aria-label="Job" disabled={!clientId && !several} onChange={(event) => pickJob(event.target.value)}>
            <option value="">{clientId ? jobs.length ? 'No job chosen' : 'No open jobs available' : 'Choose a client first'}</option>
            {owned.map((item) => <option key={item.id} value={`app:${item.id}`}>{applicationOptionText(item, item.id === activeApplicationId)}</option>)}
            {pickableJobs.map(({ job, reason }) => (
              <option key={job.orderId} value={job.orderId} disabled={Boolean(reason)}>{jobOptionText(job)}{reason ? ` (${reason})` : ''}</option>
            ))}
          </Select>
        ) : several ? (
          <Select id="af-assign-job" value={jobSelectValue} aria-label="Job" onChange={(event) => pickJob(event.target.value)}>
            {owned.map((item) => <option key={item.id} value={`app:${item.id}`}>{applicationOptionText(item, item.id === activeApplicationId)}</option>)}
          </Select>
        ) : (
          <p className="af-assign-value" id="af-assign-job" title={assignment.job?.title}>{assignment.job ? (assignment.job.title || assignment.job.ref) : NOT_SET}</p>
        )}
      </Field>

      <Field id="source" label="Source" saving={saving.source} error={errors.source}>
        {can.source ? (
          <Select id="af-assign-source" value={sourceKey} aria-label="Source" onChange={(event) => { if (event.target.value && event.target.value !== sourceKey) onChange('source', { source: event.target.value }) }}>
            {!sourceKey && <option value="">Select a source</option>}
            {sourceOptions.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
          </Select>
        ) : (
          <p className="af-assign-value" id="af-assign-source">{assignment.source.label || NOT_SET}</p>
        )}
      </Field>

      <Field id="agency" label="Agency" saving={saving.agency} error={errors.agency}>
        {can.agency ? (
          <SuggestInput
            id="af-assign-agency"
            value={agencyText}
            options={options.agencies.map((item) => item.name)}
            placeholder="Choose or type a name"
            aria-label="Agency"
            autoComplete="off"
            onChange={setAgencyText}
            onBlur={commitAgency}
            onKeyDown={(event) => { if (event.key === 'Enter' && !event.defaultPrevented) { event.preventDefault(); commitAgency() } }}
          />
        ) : (
          <p className="af-assign-value" id="af-assign-agency">{cleanAgencyName(assignment.agency?.name) || NOT_SET}</p>
        )}
      </Field>

      <Field id="recruiter" label="Recruiter" saving={saving.recruiter} error={errors.recruiter}>
        {can.recruiter ? (
          <Select id="af-assign-recruiter" searchable value={assignment.recruiter?.id ?? ''} aria-label="Recruiter" onChange={(event) => { if (event.target.value && event.target.value !== assignment.recruiter?.id) onChange('recruiter', { recruiterId: event.target.value }) }}>
            {!assignment.recruiter && <option value="">Not assigned</option>}
            {assignment.recruiter && !options.recruiters.some((item) => item.id === assignment.recruiter?.id) && (
              <option value={assignment.recruiter.id} disabled>{assignment.recruiter.name}</option>
            )}
            {options.recruiters.map((item) => <option key={item.id} value={item.id}>{item.name}{item.role ? ` (${roleLabel(item.role).toLowerCase()})` : ''}</option>)}
          </Select>
        ) : (
          <p className="af-assign-value" id="af-assign-recruiter">{assignment.recruiter?.name || NOT_ASSIGNED_TEXT}</p>
        )}
      </Field>
      {stageControl}
    </section>
  )
}
