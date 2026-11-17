import { useId, useState } from 'react'
import { ChevronDown, SlidersHorizontal } from 'lucide-react'
import { Field, Select } from '@/shared/ui/primitives'

interface Props {
  order: string
  onOrder: (value: string) => void
  recruiter: string
  onRecruiter: (value: string) => void
  jobs: { id: string; label: string }[]
  recruiters: { id: string; name: string }[]
  me: { id: string; name: string }
  importsForSelf: boolean
  disabled: boolean
}

/** The optional job and recruiter: one quiet line ("No job · Me") that opens into the two choices. Nothing here blocks the import. */
export function DefaultsRow({ order, onOrder, recruiter, onRecruiter, jobs, recruiters, me, importsForSelf, disabled }: Props) {
  const [open, setOpen] = useState(false)
  const panel = useId()
  const jobText = jobs.find((job) => job.id === order)?.label ?? 'No job'
  const recruiterText = importsForSelf || !recruiter ? 'Me' : recruiters.find((r) => r.id === recruiter)?.name ?? 'Me'
  return (
    <div className="ic-defaults">
      <button type="button" className="ic-defaults-toggle" aria-expanded={open} aria-controls={panel} onClick={() => setOpen((v) => !v)}>
        <SlidersHorizontal aria-hidden="true" />
        <span className="ic-defaults-title">Job and recruiter <em>(optional)</em></span>
        <span className="ic-defaults-value">{jobText} · {recruiterText}</span>
        <ChevronDown className="ic-chevron" aria-hidden="true" />
      </button>
      {open && (
        <div id={panel} className="ic-defaults-body">
          <Field label="Job order" hint="The job for everyone below. Open a candidate to give them a different job.">
            <Select value={order} onChange={(e) => onOrder(e.target.value)} disabled={disabled}>
              <option value="">No job</option>
              {jobs.map((job) => <option key={job.id} value={job.id}>{job.label}</option>)}
            </Select>
          </Field>
          {importsForSelf ? (
            <Field label="Recruiter" hint="Candidates you import are assigned to you.">
              <Select value={me.id} disabled><option value={me.id}>{me.name}</option></Select>
            </Field>
          ) : (
            <Field label="Recruiter" hint="Who owns these candidates.">
              <Select value={recruiter} onChange={(e) => onRecruiter(e.target.value)} disabled={disabled}>
                <option value="">Me ({me.name})</option>
                {recruiters.filter((u) => u.id !== me.id).map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
              </Select>
            </Field>
          )}
          {!order && <p className="ic-defaults-note">No job chosen: candidates are saved with their documents and no job attached. You can add a job later.</p>}
        </div>
      )}
    </div>
  )
}
