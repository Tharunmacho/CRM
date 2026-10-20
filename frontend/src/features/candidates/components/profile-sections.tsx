import { useState, type ReactNode } from 'react'
import { ShieldCheck } from 'lucide-react'
import type { Candidate } from '@/shared/model/types'
import { dateLabel, money } from '@/shared/lib/format'
import { Badge, Panel, PanelHeader } from '@/shared/ui/display'
import { Button } from '@/shared/ui/primitives'

/**
 * The Veris output stored on the candidate (GET /candidates/:id/ocr). Kept in Veris's
 * own snake_case, exactly as it arrived, and every field is optional: no two resumes
 * come back with the same parts filled in.
 */
export interface ResumeOcr {
  contact?: {
    emails?: string[]
    linkedin?: string | null
    github?: string | null
    websites?: string[]
    address?: string | null
    whatsapp?: string | null
  }
  personal_info?: {
    gender?: string | null
    father_name?: string | null
    mother_name?: string | null
    place_of_birth?: string | null
    religion?: string | null
    marital_status?: string | null
    languages_known?: string[]
  }
  designation?: string | null
  industry?: string | null
  highest_qualification?: string | null
  experience?: {
    title?: string | null
    company?: string | null
    location?: string | null
    country?: string | null
    is_overseas?: boolean
    start?: string | null
    end?: string | null
    duration_months?: number
    duration_human?: string
    description?: string | null
  }[]
  total_experience_months?: number
  total_experience_human?: string
  indian_experience_months?: number
  indian_experience_human?: string
  overseas_experience_months?: number
  overseas_experience_human?: string
  skills?: string[]
  certifications?: string[]
  achievements?: string[]
  projects?: { name?: string | null; description?: string | null }[]
  education?: { degree?: string | null; institution?: string | null; start?: string | null; end?: string | null }[]
}

export interface CandidateOcr {
  resume?: ResumeOcr
  passport?: { mrz?: { all_check_digits_valid?: boolean } | null }
}

const SOURCE_LABEL: Record<string, string> = {
  walk_in: 'Walk-in',
  referral: 'Referral',
  email: 'Email',
  whatsapp: 'WhatsApp',
  other: 'Other',
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "2019-06" reads as "Jun 2019". A bare year, or anything else, is shown as written. */
function monthLabel(value?: string | null) {
  const v = (value ?? '').trim()
  const m = /^(\d{4})-(\d{2})$/.exec(v)
  if (m && Number(m[2]) >= 1 && Number(m[2]) <= 12) return `${MONTHS[Number(m[2]) - 1]} ${m[1]}`
  return v
}

function period(start?: string | null, end?: string | null) {
  const s = monthLabel(start)
  const e = monthLabel(end)
  if (s && e) return s === e ? s : `${s} – ${e}`
  if (s) return `From ${s}`
  if (e) return `Until ${e}`
  return ''
}

function capitalise(v?: string | null) {
  return v ? v.charAt(0).toUpperCase() + v.slice(1) : ''
}

/** Only an experience figure Veris actually counted; zero months means it read none. */
function span(months?: number, human?: string) {
  return months && months > 0 ? human : undefined
}

function ExternalLink({ href }: { href: string }) {
  const url = /^https?:\/\//i.test(href) ? href : `https://${href}`
  return (
    <a href={url} target="_blank" rel="noreferrer" className="break-all text-navy-700 hover:underline">
      {href}
    </a>
  )
}

/** Lets a long address wrap at the "@" rather than mid-word in a narrow column. */
function EmailAddress({ value }: { value: string }) {
  const at = value.indexOf('@')
  if (at <= 0) return <>{value}</>
  return (
    <>
      {value.slice(0, at)}
      <wbr />
      {value.slice(at)}
    </>
  )
}

type Fact = [label: string, value: ReactNode]

const isFilled = (v: ReactNode) => v !== null && v !== undefined && v !== false && v !== ''

/**
 * A titled group of label/value pairs. Pairs without a value are dropped, and a group
 * left with none is not drawn at all — an empty heading is noise on a profile.
 */
function FactSection({ title, action, facts }: { title: string; action?: ReactNode; facts: Fact[] }) {
  const shown = facts.filter(([, v]) => isFilled(v))
  if (shown.length === 0) return null
  return (
    <Panel flush>
      <PanelHeader title={title} action={action} />
      <dl className="grid gap-x-6 gap-y-4 px-5 py-4 sm:grid-cols-2 lg:grid-cols-3">
        {shown.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-micro text-ink-400">{label}</dt>
            {/* Wraps rather than truncating: addresses and names run long. */}
            <dd className="mt-0.5 break-words text-sm text-ink">{value}</dd>
          </div>
        ))}
      </dl>
    </Panel>
  )
}

function BulletSection({ title, items }: { title: string; items?: string[] }) {
  const shown = (items ?? []).map((s) => s.trim()).filter(Boolean)
  if (shown.length === 0) return null
  return (
    <Panel flush>
      <PanelHeader title={title} />
      <ul className="list-disc space-y-1.5 py-4 pl-10 pr-5 text-sm text-ink-700 marker:text-ink-300">
        {shown.map((s, i) => (
          <li key={i}>{s}</li>
        ))}
      </ul>
    </Panel>
  )
}

function ScannedText({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  return (
    <Panel flush>
      <PanelHeader
        title="Scanned resume text"
        meta="Exactly what the OCR read — this is what the Data Bank searches"
        action={
          <Button size="sm" variant="ghost" onClick={() => setOpen((v) => !v)}>
            {open ? 'Hide' : 'Show'}
          </Button>
        }
      />
      {open && <p className="whitespace-pre-line px-5 py-4 text-sm leading-relaxed text-ink-600">{text}</p>}
    </Panel>
  )
}

/**
 * Everything known about the candidate, grouped under the heading it belongs to. The
 * saved record comes first; the stored resume fills in what the form never asked for.
 */
export function CandidateSections({ cand, ocr }: { cand: Candidate; ocr: CandidateOcr }) {
  const r = ocr.resume
  const contact = r?.contact
  const personal = r?.personal_info

  const jobs = [...(r?.experience ?? [])]
    .filter((j) => j.title || j.company)
    // Newest first; an entry without a start date sinks to the bottom.
    .sort((a, b) => (b.start ?? '').localeCompare(a.start ?? ''))
  const schools = (r?.education ?? []).filter((e) => e.degree || e.institution)
  const projects = (r?.projects ?? []).filter((p) => p.name)
  const skills = cand.skills.length ? cand.skills : (r?.skills ?? [])

  const total = span(r?.total_experience_months, r?.total_experience_human)
  const overseas = span(r?.overseas_experience_months, r?.overseas_experience_human)
  const experienceMeta = [total && `${total} in total`, overseas && `${overseas} overseas`]
    .filter(Boolean)
    .join(' · ')

  return (
    <>
      <FactSection
        title="Contact details"
        facts={[
          ['Phone', <span className="ident">{cand.phone}</span>],
          ['WhatsApp', (cand.whatsapp || contact?.whatsapp) && <span className="ident">{cand.whatsapp || contact?.whatsapp}</span>],
          ['Email', (cand.email || contact?.emails?.[0]) && <EmailAddress value={(cand.email || contact?.emails?.[0])!} />],
          ['Current city', cand.currentCity],
          ['Address', contact?.address],
          ['LinkedIn', contact?.linkedin && <ExternalLink href={contact.linkedin} />],
          ['GitHub', contact?.github && <ExternalLink href={contact.github} />],
          [
            'Websites',
            contact?.websites?.length ? (
              <span className="flex flex-col">
                {contact.websites.map((w) => (
                  <ExternalLink key={w} href={w} />
                ))}
              </span>
            ) : null,
          ],
        ]}
      />

      <FactSection
        title="Personal details"
        facts={[
          ['Date of birth', cand.dob && dateLabel(cand.dob)],
          ['Gender', capitalise(cand.gender) || capitalise(personal?.gender)],
          ['Nationality', cand.nationality],
          ['Languages', (cand.languages.length ? cand.languages : (personal?.languages_known ?? [])).join(', ')],
          ["Father's name", personal?.father_name],
          ["Mother's name", personal?.mother_name],
          ['Marital status', capitalise(personal?.marital_status)],
          ['Place of birth', personal?.place_of_birth],
          ['Religion', personal?.religion],
        ]}
      />

      <FactSection
        title="Passport details"
        action={
          ocr.passport?.mrz?.all_check_digits_valid ? (
            <Badge tone="navy">
              <ShieldCheck className="h-3 w-3" />
              MRZ verified
            </Badge>
          ) : undefined
        }
        facts={[
          ['Passport number', cand.passportNo && <span className="ident">{cand.passportNo}</span>],
          ['Place of issue', cand.passportPlace],
          ['Issue date', cand.passportIssue && dateLabel(cand.passportIssue)],
          ['Expiry date', cand.passportExpiry && dateLabel(cand.passportExpiry)],
        ]}
      />

      <FactSection
        title="Professional summary"
        facts={[
          ['Designation', cand.designations.join(', ') || r?.designation],
          ['Industry', r?.industry],
          ['Highest qualification', r?.highest_qualification],
          ['Total experience', total ?? (cand.totalExperience > 0 ? `${cand.totalExperience} years` : null)],
          ['Experience in India', span(r?.indian_experience_months, r?.indian_experience_human)],
          ['Overseas experience', overseas ?? (cand.gulfExperience > 0 ? `${cand.gulfExperience} years` : null)],
          ['Expected salary', cand.expectedSalary != null && money(cand.expectedSalary)],
          [
            'Source',
            `${SOURCE_LABEL[cand.source] ?? cand.source}${cand.sourceDetail ? ` · ${cand.sourceDetail}` : ''}`,
          ],
        ]}
      />

      {jobs.length > 0 && (
        <Panel flush>
          <PanelHeader title="Work experience" meta={experienceMeta || undefined} />
          <ol className="px-6 pb-2 pt-5">
            {jobs.map((j, i) => (
              <li key={i} className="relative border-l border-rule pb-6 pl-6 last:border-transparent last:pb-4">
                <span className="absolute -left-[5px] top-1 h-2.5 w-2.5 rounded-full bg-navy-600 ring-4 ring-white" />
                <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                  <p className="text-sm font-semibold text-ink">
                    {j.title || 'Role not stated'}
                    {j.company && <span className="font-normal text-ink-500"> · {j.company}</span>}
                  </p>
                  <p className="text-micro text-ink-400 tnum">
                    {[period(j.start, j.end), span(j.duration_months, j.duration_human)].filter(Boolean).join(' · ')}
                  </p>
                </div>
                {(j.location || j.country || j.is_overseas) && (
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-ink-500">
                    {j.location || j.country}
                    {j.is_overseas && <Badge tone="navy">Overseas</Badge>}
                  </div>
                )}
                {j.description?.trim() && (
                  <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-ink-600">{j.description.trim()}</p>
                )}
              </li>
            ))}
          </ol>
        </Panel>
      )}

      {schools.length > 0 && (
        <Panel flush>
          <PanelHeader title="Education" />
          <ul className="divide-y divide-rule-soft">
            {schools.map((e, i) => (
              <li key={i} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-5 py-3.5">
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-ink">{e.degree || e.institution}</p>
                  {e.degree && e.institution && <p className="mt-0.5 text-xs text-ink-500">{e.institution}</p>}
                </div>
                {period(e.start, e.end) && <p className="text-micro text-ink-400 tnum">{period(e.start, e.end)}</p>}
              </li>
            ))}
          </ul>
        </Panel>
      )}

      {skills.length > 0 && (
        <Panel flush>
          <PanelHeader title="Skills" />
          <div className="flex flex-wrap gap-1.5 px-5 py-4">
            {skills.map((s) => (
              <Badge key={s}>{s}</Badge>
            ))}
          </div>
        </Panel>
      )}

      <BulletSection title="Certifications" items={r?.certifications} />
      <BulletSection title="Achievements" items={r?.achievements} />

      {projects.length > 0 && (
        <Panel flush>
          <PanelHeader title="Projects" />
          <ul className="divide-y divide-rule-soft">
            {projects.map((p, i) => (
              <li key={i} className="px-5 py-3.5">
                <p className="text-sm font-semibold text-ink">{p.name}</p>
                {p.description?.trim() && <p className="mt-1 text-sm leading-relaxed text-ink-600">{p.description.trim()}</p>}
              </li>
            ))}
          </ul>
        </Panel>
      )}

      {/* Only a scanned resume has text worth reading; a hand-filled one holds a summary line. */}
      {r && cand.resumeText?.trim() && <ScannedText text={cand.resumeText.trim()} />}
    </>
  )
}
