/**
 * The candidate profile as plain data: what each section shows, merged from the saved candidate
 * record and the stored Veris reading. No React, so it can be tested directly. Nothing is invented:
 * a value that neither source holds simply produces no row, and a section with no rows is empty.
 *
 * Merge rule: the saved candidate record wins when it holds a value, otherwise the Veris value is used.
 */
import type { Candidate } from '@/shared/model/types'
import type { CandidateOcr } from './components/ocr-types'
import { humanDate, monthIndex, primaryDocument } from './ocr-view'

export type RowKind = 'text' | 'ident' | 'email' | 'link' | 'list'

export interface ProfileRow {
  label: string
  value: string
  kind?: RowKind
  /** Present for `list` rows: one entry per line. */
  items?: string[]
}

export interface JobView {
  title: string
  company: string
  location: string
  overseas: boolean
  period: string
  duration: string
  description: string
}

export interface EducationView {
  degree: string
  institution: string
  period: string
  grade: string
}

export interface ContactChip {
  kind: 'email' | 'phone' | 'whatsapp' | 'location'
  value: string
}

export interface ProfileModel {
  designation: string
  /** Muted line under the designation: source file, channel, nationality, ingested date. */
  meta: string[]
  contacts: ContactChip[]
  /** The Veris "Candidate details" card: who they are and how to reach them. */
  details: ProfileRow[]
  /** What the recruiter recorded about the job they want; empty for most email intakes. */
  job: ProfileRow[]
  summary: string
  /** Everything else Veris read about the person: personal particulars and experience splits. */
  additional: ProfileRow[]
  jobs: JobView[]
  experienceMeta: string
  skills: string[]
  education: EducationView[]
  certifications: string[]
  achievements: string[]
  projects: { name: string; description: string }[]
  passport: ProfileRow[]
  sourceFileName: string
}

export interface ProfileFormat {
  money: (amount: number) => string
  date: (value: string) => string
}

export const SOURCE_LABEL: Record<string, string> = {
  walk_in: 'Walk-in',
  referral: 'Referral',
  email: 'Email',
  whatsapp: 'WhatsApp',
  other: 'Other',
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export const clean = (v?: string | null): string => (typeof v === 'string' ? v.trim() : '')
export const capitalise = (v?: string | null): string => {
  const t = clean(v)
  return t ? t.charAt(0).toUpperCase() + t.slice(1) : ''
}

/** "2019-06" reads as "Jun 2019". A bare year, or anything else, is shown as written. */
export function monthLabel(value?: string | null): string {
  const v = clean(value)
  const m = /^(\d{4})-(\d{1,2})$/.exec(v)
  if (m && Number(m[2]) >= 1 && Number(m[2]) <= 12) return `${MONTHS[Number(m[2]) - 1]} ${m[1]}`
  return v
}

export function periodLabel(start?: string | null, end?: string | null): string {
  const s = monthLabel(start)
  const e = monthLabel(end)
  if (s && e) return s === e ? s : `${s} – ${e}`
  if (s) return `From ${s}`
  if (e) return `Until ${e}`
  return ''
}

/**
 * A sortable "months since year 0" for the free-text dates on a resume: "2019-06", "Jun 2019",
 * "06/2019", "2019". "Present" sorts above everything; unreadable input sorts last (-1).
 */
export function dateKey(value?: string | null): number {
  const t = clean(value).toLowerCase()
  if (!t) return -1
  if (/^(present|current|currently|now|till date|to date|ongoing|today)\b/.test(t)) return Number.POSITIVE_INFINITY
  let m = /^(\d{4})-(\d{1,2})/.exec(t)
  if (m) return Number(m[1]) * 12 + Number(m[2]) - 1
  m = /^(\d{1,2})[/\-.](\d{4})$/.exec(t)
  if (m) return Number(m[2]) * 12 + Number(m[1]) - 1
  m = /([a-z]{3,9})\.?,?\s+(\d{4})/.exec(t)
  if (m && monthIndex(m[1]) >= 0) return Number(m[2]) * 12 + monthIndex(m[1])
  m = /\b(\d{4})\b/.exec(t)
  return m ? Number(m[1]) * 12 : -1
}

/** Only an experience figure Veris actually counted; zero months means it read none. */
const span = (months?: number, human?: string): string => (months && months > 0 ? clean(human) : '')

const years = (n?: number): string => (typeof n === 'number' && n > 0 ? `${n} ${n === 1 ? 'year' : 'years'}` : '')

const digits = (v?: string | null) => (v ?? '').replace(/\D/g, '')

const row = (label: string, value: string | null | undefined, kind: RowKind = 'text'): ProfileRow | null => {
  const v = clean(value)
  return v ? { label, value: v, kind } : null
}

const listRow = (label: string, items: (string | null | undefined)[] | undefined, kind: RowKind = 'list'): ProfileRow | null => {
  const shown = (items ?? []).map((i) => clean(i)).filter(Boolean)
  return shown.length ? { label, value: shown.join(', '), kind, items: shown } : null
}

const present = (rows: (ProfileRow | null)[]): ProfileRow[] => rows.filter((r): r is ProfileRow => r !== null)

const unique = (items: string[]) => [...new Set(items)]

export function buildProfileModel(cand: Candidate, ocr: CandidateOcr | null | undefined, fmt: ProfileFormat): ProfileModel {
  const resumeDoc = primaryDocument(ocr, 'resume')
  const r = (resumeDoc?.payload ?? undefined) as CandidateOcr['resume']
  const contact = r?.contact
  const personal = r?.personal_info
  const sourceFileName = ['resume', 'passport', 'aadhaar']
    .map((kind) => primaryDocument(ocr, kind)?.source.fileName ?? '')
    .find(Boolean) ?? ''

  const jobs: JobView[] = [...(r?.experience ?? [])]
    .filter((j) => clean(j.title) || clean(j.company))
    // Newest first; the current job ("Present") leads, an entry without a readable start sinks.
    .sort((a, b) => dateKey(b.start) - dateKey(a.start) || dateKey(b.end) - dateKey(a.end))
    .map((j) => ({
      title: clean(j.title),
      company: clean(j.company),
      location: clean(j.location) || clean(j.country),
      overseas: !!j.is_overseas,
      period: periodLabel(j.start, j.end),
      duration: span(j.duration_months, j.duration_human),
      description: clean(j.description),
    }))

  const education: EducationView[] = (r?.education ?? [])
    .filter((e) => clean(e.degree) || clean(e.institution))
    .map((e) => ({ degree: clean(e.degree), institution: clean(e.institution), period: periodLabel(e.start, e.end), grade: clean(e.grade) }))

  const total = span(r?.total_experience_months, r?.total_experience_human)
  const overseas = span(r?.overseas_experience_months, r?.overseas_experience_human)
  const experienceMeta = [total && `${total} in total`, overseas && `${overseas} overseas`].filter(Boolean).join(' · ')

  const designation = cand.designations.map(clean).filter(Boolean).join(', ') || clean(r?.designation)
  const email = clean(cand.email) || clean(contact?.emails?.[0])
  const whatsapp = clean(cand.whatsapp) || clean(contact?.whatsapp)
  const phone = clean(cand.phone)
  const city = clean(cand.currentCity)
  const address = clean(contact?.address)

  const contacts: ContactChip[] = []
  if (email) contacts.push({ kind: 'email', value: email })
  if (phone) contacts.push({ kind: 'phone', value: phone })
  if (whatsapp && digits(whatsapp) !== digits(phone)) contacts.push({ kind: 'whatsapp', value: whatsapp })
  if (city || address) contacts.push({ kind: 'location', value: city || address })

  const channel = SOURCE_LABEL[cand.source] ?? clean(cand.source)
  const docChannel = clean(resumeDoc?.source.channel)
  const meta = [
    sourceFileName,
    channel && (cand.source === 'other' && docChannel ? capitalise(docChannel) : channel),
    cand.addedAt ? `Ingested ${fmt.date(cand.addedAt)}` : '',
  ].filter(Boolean)

  const languages = cand.languages.length ? cand.languages : (personal?.languages_known ?? [])
  const dob = clean(cand.dob) || clean(personal?.date_of_birth)

  const totalExperience = cand.totalExperience > 0 ? years(cand.totalExperience) : total
  const overseasExperience = cand.gulfExperience > 0 ? years(cand.gulfExperience) : overseas
  const industry = clean(r?.industry)
  const qualification = clean(r?.highest_qualification)

  // Same order as the Veris dashboard: identity and role first, then how to reach them.
  const details = present([
    row('Name', cand.name),
    row('Designation', designation),
    row('Industry', industry),
    row('Highest qualification', qualification),
    row('Total experience', totalExperience),
    listRow('Languages', languages),
    row('Email', email, 'email'),
    row('Phone', phone, 'ident'),
    row('WhatsApp', whatsapp && digits(whatsapp) !== digits(phone) ? whatsapp : '', 'ident'),
    row('LinkedIn', contact?.linkedin, 'link'),
    row('GitHub', contact?.github, 'link'),
    listRow('Websites', contact?.websites, 'link'),
    row('Address', address),
    row('Current city', city && city !== address ? city : ''),
  ])

  const job = present([
    row('Expected salary', cand.expectedSalary != null ? fmt.money(cand.expectedSalary) : ''),
  ])

  const additional = present([
    row('Date of birth', humanDate(dob)),
    row('Gender', capitalise(cand.gender) || capitalise(personal?.gender)),
    row('Nationality', clean(cand.nationality) || clean(personal?.nationality)),
    row('Marital status', capitalise(personal?.marital_status)),
    row("Father's name", personal?.father_name),
    row("Mother's name", personal?.mother_name),
    row('Place of birth', personal?.place_of_birth),
    row('Religion', personal?.religion),
    row('Experience in India', span(r?.indian_experience_months, r?.indian_experience_human)),
    row('Overseas experience', overseasExperience),
  ])

  const pd = r?.passport_details
  const passport = present([
    row('Passport number', clean(cand.passportNo) || clean(pd?.passport_number), 'ident'),
    row('Place of issue', clean(cand.passportPlace) || clean(pd?.place_of_issue)),
    row('Issue date', humanDate(clean(cand.passportIssue) || clean(pd?.date_of_issue))),
    row('Expiry date', humanDate(clean(cand.passportExpiry) || clean(pd?.date_of_expiry))),
  ])

  const list = (items?: string[]) => unique((items ?? []).map(clean).filter(Boolean))
  return {
    designation,
    meta,
    contacts,
    details,
    job,
    summary: clean(r?.summary),
    additional,
    jobs,
    experienceMeta,
    skills: cand.skills.length ? cand.skills.map(clean).filter(Boolean) : list(r?.skills),
    education,
    certifications: list(r?.certifications),
    achievements: list(r?.achievements),
    projects: (r?.projects ?? []).filter((p) => clean(p.name)).map((p) => ({ name: clean(p.name), description: clean(p.description) })),
    passport,
    sourceFileName,
  }
}
