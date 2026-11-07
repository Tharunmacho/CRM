/**
 * Pure helpers behind the New job / Edit job dialog: the client list it offers, the job-title decision (use a saved title or
 * add a new one), validation, the request body and the mapping of server errors to the right box. No React, so they are unit tested.
 */
import { filterByText } from './job-search'

export type JobFieldErrors = Record<string, string>

/* ------------------------------------------------------------------ job title */

/** A typed title as the server should keep it: extra spaces gone. */
export const cleanTitle = (value: string | null | undefined) => (value ?? '').replace(/\s+/g, ' ').trim()

/** Two titles are the same job title when they only differ by case or spacing. */
export const titleKey = (value: string | null | undefined) => cleanTitle(value).toLowerCase()

export type TitleDecision =
  | { kind: 'empty' }
  | { kind: 'existing'; id: string; title: string }
  | { kind: 'new'; title: string }

/**
 * What to do with the typed job title: nothing typed, a title that is already saved (use its id, never add a copy), or a new one to
 * create first. `created` holds titles this dialog already added (title key -> id) so a second try after a failed save reuses them.
 */
export function decideTitle(typed: string, designations: { id: string; title: string }[], created?: ReadonlyMap<string, string>): TitleDecision {
  const title = cleanTitle(typed)
  if (!title) return { kind: 'empty' }
  const key = titleKey(title)
  const saved = designations.find((item) => titleKey(item.title) === key)
  if (saved) return { kind: 'existing', id: saved.id, title: saved.title }
  const made = created?.get(key)
  if (made) return { kind: 'existing', id: made, title }
  return { kind: 'new', title }
}

/* -------------------------------------------------------------- client options */

/** One line of the client list: a company, with the contact person of the client it belongs to. */
export interface ClientOption {
  /** The company's id: the job is placed for this company. */
  companyId: string
  clientId: string
  company: string
  contact: string
  country: string
  city: string
  /** Jobs already placed for this company. */
  jobs: number
  /** Jobs already placed for the whole client (all its companies). */
  clientJobs: number
}

interface ClientLike { id: string; name: string; status: string; country?: string | null; city?: string | null }
interface CompanyLike { id: string; clientId: string; name: string; status: string; country?: string | null; city?: string | null }
interface OrderLike { companyId: string }

/**
 * The clients a job can be placed for, one entry per company ("Company (contact person)"), so a client with several companies
 * appears several times. Inactive clients and companies are left out, except the one already chosen (editing an old job). A client
 * with jobs already stays in the list: it can take as many as it needs.
 */
export function buildClientOptions(input: { clients: ClientLike[]; companies: CompanyLike[]; orders: OrderLike[]; keepCompanyId?: string; includeInactive?: boolean }): ClientOption[] {
  const clientOf = new Map(input.clients.map((client) => [client.id, client]))
  const byCompany = new Map<string, number>()
  const byClient = new Map<string, number>()
  const owner = new Map(input.companies.map((company) => [company.id, company.clientId]))
  for (const order of input.orders) {
    byCompany.set(order.companyId, (byCompany.get(order.companyId) ?? 0) + 1)
    const clientId = owner.get(order.companyId)
    if (clientId) byClient.set(clientId, (byClient.get(clientId) ?? 0) + 1)
  }
  const options: ClientOption[] = []
  for (const company of input.companies) {
    const client = clientOf.get(company.clientId)
    if (!client) continue
    const keep = company.id === input.keepCompanyId
    if (!keep && !input.includeInactive && (company.status !== 'active' || client.status !== 'active')) continue
    options.push({
      companyId: company.id, clientId: client.id, company: company.name, contact: client.name,
      country: company.country || client.country || '', city: company.city || client.city || '',
      jobs: byCompany.get(company.id) ?? 0, clientJobs: byClient.get(client.id) ?? 0,
    })
  }
  return options.sort((a, b) => a.company.localeCompare(b.company) || a.contact.localeCompare(b.contact))
}

/** The same entry as one line of text: "Gulf Build (Khalid Rahman)". */
export const optionLabel = (option: Pick<ClientOption, 'company' | 'contact'>) => option.contact ? `${option.company} (${option.contact})` : option.company

/** "No jobs yet", "1 job", "2 jobs". */
export const jobsLabel = (count: number) => count <= 0 ? 'No jobs yet' : count === 1 ? '1 job' : `${count} jobs`

/** Options that match the typed words (every word, any part of the company, contact person, country or city). */
export function filterClientOptions(options: ClientOption[], query: string): ClientOption[] {
  return filterByText(options, query, (option) => [option.company, option.contact, option.country, option.city])
}

/** The clients the Jobs list can be filtered by: every company that already has at least one job (inactive ones too). */
export function clientFilterOptions(input: { clients: ClientLike[]; companies: CompanyLike[]; orders: OrderLike[] }): ClientOption[] {
  return buildClientOptions({ ...input, includeInactive: true }).filter((option) => option.jobs > 0)
}

/* ------------------------------------------------------------------- the form */

/** Everything typed in the dialog, as strings. */
export interface JobForm {
  companyId: string
  title: string
  /** The job title's requirements (stored on the title, shared by every job with it). */
  skills: string[]
  minExperience: string
  description: string
  openings: string
  country: string
  city: string
  salary: string
  currency: string
  deadline: string
  benefits: string
  contractPeriod: string
  interviewDate: string
  status: string
}

export const emptyJobForm = (companyId: string, currency: string): JobForm => ({
  companyId, title: '', skills: [], minExperience: '0', description: '', openings: '1', country: '', city: '', salary: '', currency, deadline: '', benefits: '', contractPeriod: '', interviewDate: '', status: 'open',
})

/** "Save and add another job": the same client (and currency) stay, everything about the job itself starts over. */
export const nextJobForm = (form: JobForm): JobForm => emptyJobForm(form.companyId, form.currency)

export const benefitList = (text: string) => text.split(',').map((item) => item.trim()).filter(Boolean)

export const MAX_OPENINGS = 100000
export const MAX_SALARY = 9999999999.99

/** What the server already knows about a job being edited: seats deployed and candidates still active. */
export interface JobCounts { deployed: number; active: number }

/** Field-level problems keyed by field name, in plain English. An empty object means the form can be sent. */
export function validateJob(form: JobForm, counts: JobCounts = { deployed: 0, active: 0 }): JobFieldErrors {
  const errors: JobFieldErrors = {}
  if (!form.companyId) errors.companyId = 'Choose the client this job is for.'
  const title = cleanTitle(form.title)
  if (!title) errors.title = 'Enter or choose the job title.'
  else if (title.length > 160) errors.title = 'A job title can have up to 160 characters.'
  const experience = Number(form.minExperience)
  if (form.minExperience.trim() === '' || !Number.isInteger(experience) || experience < 0 || experience > 60) errors.minExperience = 'Enter the minimum experience as whole years, from 0 to 60.'
  if (form.skills.length > 100) errors.skills = 'A job title can have up to 100 required skills.'
  if (form.description.length > 20000) errors.description = 'The description is too long.'
  const openings = Number(form.openings)
  const floor = Math.max(1, counts.deployed)
  if (form.openings.trim() === '' || !Number.isInteger(openings) || openings < 1) errors.openings = 'Enter how many people are needed, a whole number of at least 1.'
  else if (openings > MAX_OPENINGS) errors.openings = `Openings can be at most ${MAX_OPENINGS.toLocaleString('en-US')}.`
  else if (openings < floor) errors.openings = `${counts.deployed} ${counts.deployed === 1 ? 'person is' : 'people are'} already deployed, so openings cannot be lower.`
  if (form.salary.trim() !== '') {
    const salary = Number(form.salary)
    if (!Number.isFinite(salary) || salary < 0 || salary > MAX_SALARY) errors.salary = 'Enter the monthly salary as a number, zero or more.'
  }
  if (!form.currency) errors.currency = 'Choose a currency.'
  if (form.interviewDate && form.deadline && form.interviewDate > form.deadline) errors.interviewDate = 'The interview date must be on or before the deadline.'
  if (form.status === 'filled' && !errors.openings && counts.deployed < openings) errors.status = 'Filled needs every opening to be deployed.'
  if (form.status === 'closed' && counts.active > 0) errors.status = 'Finish or remove the active candidates before closing this job.'
  return errors
}

/** Body for POST /orders and PUT /orders/:id (the server still calls a job an order). */
export function buildJobPayload(form: JobForm, designationId: string) {
  const salary = form.salary.trim()
  return {
    companyId: form.companyId,
    designationId,
    openings: Number(form.openings),
    country: form.country.trim(),
    city: form.city.trim(),
    salary: salary === '' ? null : Number(salary),
    currency: form.currency,
    benefits: benefitList(form.benefits),
    contractPeriod: form.contractPeriod.trim(),
    interviewDate: form.interviewDate || null,
    deadline: form.deadline || null,
    status: form.status || 'open',
  }
}

/* -------------------------------------------------------------- server errors */

const FIELD_OF: Record<string, string> = {
  companyid: 'companyId', designationid: 'title', title: 'title', requiredskills: 'skills', minexperience: 'minExperience', description: 'description', openings: 'openings', country: 'country', city: 'city', salary: 'salary',
  currency: 'currency', benefits: 'benefits', contractperiod: 'contractPeriod', interviewdate: 'interviewDate', deadline: 'deadline', status: 'status',
}

function sentence(message: string) {
  const text = message.trim()
  return /[.!?]$/.test(text) ? text : text + '.'
}

/** Which box a plain server message (HTTP 404/409 text) is about, or '' when it is about the job as a whole. */
function fieldOfMessage(message: string): string {
  if (/interview date/i.test(message)) return 'interviewDate'
  if (/openings|deployments/i.test(message)) return 'openings'
  if (/marked filled|closing/i.test(message)) return 'status'
  if (/currency/i.test(message)) return 'currency'
  if (/company|client/i.test(message)) return 'companyId'
  if (/job title|designation/i.test(message)) return 'title'
  return ''
}

/**
 * Turns the server's error body into field errors so each message shows under its box. A 422 body is a list of
 * `{loc: ['body', 'openings'], msg}`; a 404/409 body is one sentence. Whatever belongs to no box comes back in `general`.
 */
export function jobErrorsFromApi(detail: unknown, fallback = ''): { fields: JobFieldErrors; general: string } {
  const fields: JobFieldErrors = {}
  const general: string[] = []
  if (Array.isArray(detail)) {
    for (const item of detail) {
      const message = typeof item?.msg === 'string' ? item.msg : ''
      if (!message) continue
      const loc: unknown[] = Array.isArray(item.loc) ? item.loc.filter((part: unknown) => part !== 'body') : []
      const key = typeof loc[0] === 'string' ? FIELD_OF[loc[0].toLowerCase()] : undefined
      if (key) { if (!fields[key]) fields[key] = sentence(message.replace(/^value error, /i, '')) }
      else general.push(sentence(message.replace(/^value error, /i, '')))
    }
    return { fields, general: general.join(' ') }
  }
  const text = typeof detail === 'string' ? detail : fallback
  if (!text) return { fields, general: '' }
  const key = fieldOfMessage(text)
  if (key) fields[key] = sentence(text)
  else general.push(sentence(text))
  return { fields, general: general.join(' ') }
}

/* ----------------------------------------------------- requirements on the job title */

/** What a job title carries for matching: the required skills, the minimum years of experience and a description. */
export interface Requirements { skills: string[]; minExperience: string; description: string }

interface DesignationLike { id: string; title: string; category?: string | null; description?: string | null; requiredSkills?: string[]; minExperience?: number }

/** The form values for a saved job title (shown when the title is chosen). A new title starts empty. */
export const requirementsOf = (designation?: DesignationLike | null): Requirements => ({
  skills: [...(designation?.requiredSkills ?? [])], minExperience: String(designation?.minExperience ?? 0), description: designation?.description ?? '',
})

/** Skills trimmed, spaces collapsed, no repeats (case blind, first spelling wins). */
export function cleanSkills(skills: string[]): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const raw of skills) {
    const skill = raw.replace(/\s+/g, ' ').trim()
    const key = skill.toLowerCase()
    if (!skill || seen.has(key)) continue
    seen.add(key)
    out.push(skill)
  }
  return out
}

/** Whether two sets of requirements say the same thing (skills compared ignoring case, order and repeats). */
export function sameRequirements(a: Requirements, b: Requirements): boolean {
  const key = (skills: string[]) => cleanSkills(skills).map((skill) => skill.toLowerCase()).sort().join('|')
  return key(a.skills) === key(b.skills) && Number(a.minExperience) === Number(b.minExperience) && a.description.trim() === b.description.trim()
}

/** Skills already used on job titles, most used first then A to Z: what the skills box suggests. */
export function skillSuggestions(designations: { requiredSkills?: string[] }[]): string[] {
  const counts = new Map<string, { text: string; count: number }>()
  for (const designation of designations) {
    for (const skill of cleanSkills(designation.requiredSkills ?? [])) {
      const key = skill.toLowerCase()
      const entry = counts.get(key)
      if (entry) entry.count += 1; else counts.set(key, { text: skill, count: 1 })
    }
  }
  return [...counts.values()].sort((a, b) => b.count - a.count || a.text.localeCompare(b.text)).map((entry) => entry.text)
}

/** Body for POST /designations and PUT /designations/:id: the same fields the old job-title dialog sent. */
export function designationBody(title: string, category: string | null | undefined, form: Requirements) {
  return {
    title,
    category: category ?? null,
    description: form.description.trim(),
    requiredSkills: cleanSkills(form.skills),
    minExperience: Number(form.minExperience),
  }
}

export type DesignationPlan =
  | { kind: 'none' }
  | { kind: 'use'; id: string }
  | { kind: 'create'; body: ReturnType<typeof designationBody> }
  | { kind: 'update'; id: string; body: ReturnType<typeof designationBody> }

/**
 * What saving does about the job title, before the job itself is saved: nothing typed, use the saved title as it is, create a new title
 * with the requirements typed, or update the saved title because its requirements were changed (that applies to every job with the title).
 */
export function planDesignation(form: Pick<JobForm, 'title' | 'skills' | 'minExperience' | 'description'>, designations: DesignationLike[], created?: ReadonlyMap<string, string>): DesignationPlan {
  const pick = decideTitle(form.title, designations, created)
  if (pick.kind === 'empty') return { kind: 'none' }
  if (pick.kind === 'new') return { kind: 'create', body: designationBody(pick.title, null, form) }
  const saved = designations.find((item) => item.id === pick.id)
  if (!saved || sameRequirements(requirementsOf(saved), form)) return { kind: 'use', id: pick.id }
  return { kind: 'update', id: saved.id, body: designationBody(saved.title, saved.category, form) }
}
