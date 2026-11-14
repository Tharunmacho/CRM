/**
 * The assignment block when the server does not (yet) send its options or permissions.
 *
 * A server that has the v2 contract sends `assignment.options` and `assignment.can`, and its values win. A server that does
 * not would otherwise leave Client, Job and Agency as dead text, so the screen builds the same pickers from the workspace it
 * already holds (clients, companies, orders, designations, users) and the reference agencies list. Plain data in, plain data
 * out, so it is tested without a browser.
 */
import type { Client, Company, Designation, Order, User } from '@/shared/model/types'
import { cleanAgencyName, sourceLabel } from './form-model'
import type { AssignmentClientOption, AssignmentData } from './types'

export interface AssignmentSources {
  clients: readonly Client[]
  companies: readonly Company[]
  orders: readonly Order[]
  designations: readonly Designation[]
  users: readonly User[]
  /** The candidate as the workspace list holds it. */
  candidate?: { source?: string; sourceDetail?: string; ownerId?: string | null }
  /** The reference agencies; null while they load. */
  agencies: readonly { id: string; name?: string; agencyName?: string }[] | null
  me: { id: string; role: string }
  /** `form.candidate.canEdit`: the signed-in person may work this candidate. */
  canEdit: boolean
  /** The job of the candidate's active application, when the form's own payload does not name it. */
  activeOrderId?: string
}

const isManager = (role: string) => role === 'admin' || role === 'manager'

/** Who may change what, by the rules of the contract: job, source and agency by an admin, a manager or the person working the candidate; the recruiter by the admin only. */
export function fallbackCan(me: { role: string }, canEdit: boolean): AssignmentData['can'] {
  const mayEdit = isManager(me.role) || canEdit
  return { job: mayEdit, source: mayEdit, agency: mayEdit, recruiter: me.role === 'admin' }
}

/** Every client with its jobs (title, reference, openings, status), built from the workspace. Inactive clients only when they are the current one. */
export function fallbackClientOptions(sources: Pick<AssignmentSources, 'clients' | 'companies' | 'orders' | 'designations'>, currentClientId = ''): AssignmentClientOption[] {
  const titles = new Map(sources.designations.map((item) => [item.id, item.title]))
  const companiesByClient = new Map<string, Company[]>()
  for (const company of sources.companies) companiesByClient.set(company.clientId, [...(companiesByClient.get(company.clientId) ?? []), company])
  const ordersByCompany = new Map<string, Order[]>()
  for (const order of sources.orders) ordersByCompany.set(order.companyId, [...(ordersByCompany.get(order.companyId) ?? []), order])
  return sources.clients
    .filter((client) => client.status !== 'inactive' || client.id === currentClientId)
    .map((client): AssignmentClientOption => {
      const companies = companiesByClient.get(client.id) ?? []
      const jobs = companies.flatMap((company) => ordersByCompany.get(company.id) ?? [])
        .map((order) => ({ orderId: order.id, ref: order.ref, title: titles.get(order.designationId) ?? '', openings: order.openings, status: order.status as string }))
        .sort((a, b) => a.ref.localeCompare(b.ref))
      // One company: name it. Several: the client is the choice, and the job's own company is not guessed.
      const single = companies.length === 1 ? companies[0] : undefined
      return { id: client.id, name: client.name, companyId: single?.id ?? '', companyName: single?.name ?? '', jobs }
    })
    .sort((a, b) => a.name.localeCompare(b.name))
}

/** The client that owns an order, through its company. */
function clientOfOrder(sources: AssignmentSources, orderId: string) {
  const order = sources.orders.find((item) => item.id === orderId)
  const company = order ? sources.companies.find((item) => item.id === order.companyId) : undefined
  const client = company ? sources.clients.find((item) => item.id === company.clientId) : undefined
  return { order, company, client }
}

/**
 * The block the screen shows. Whatever the server sent stays as it is; what it did not send (the whole block, the options, the
 * permissions) is filled from the workspace.
 */
export function completeAssignment(assignment: AssignmentData, sources: AssignmentSources): AssignmentData {
  if (assignment.supported && assignment.serverOptions && assignment.serverCan) {
    if (!assignment.can.job) return assignment
    const existing = new Set(assignment.options.clients.map((client) => client.id))
    const missing = fallbackClientOptions(sources, assignment.client?.id ?? '').filter((client) => !existing.has(client.id))
    if (!missing.length) return assignment
    // Older assignment APIs can omit clients. The current workspace supplies active clients
    // and their jobs with status labels. Unavailable jobs remain disabled; saves are revalidated.
    return { ...assignment, options: { ...assignment.options, clients: [...assignment.options.clients, ...missing].sort((a, b) => a.name.localeCompare(b.name)) } }
  }
  let next: AssignmentData = assignment

  if (!assignment.supported) {
    const orderId = assignment.job?.orderId || sources.activeOrderId || ''
    const { order, company, client } = orderId ? clientOfOrder(sources, orderId) : { order: undefined, company: undefined, client: undefined }
    const designation = order ? sources.designations.find((item) => item.id === order.designationId) : undefined
    const sourceKey = sources.candidate?.source || assignment.source.key
    // The detail may hold an intake pointer instead of a name: that is not an agency.
    const agencyName = (value?: string) => {
      const known = sources.agencies?.some(item => (item.agencyName || item.name || '').trim().toLowerCase() === (value ?? '').trim().toLowerCase())
      return cleanAgencyName(value, known ? 'referral' : sourceKey)
    }
    const detail = agencyName(sources.candidate?.sourceDetail) || agencyName(assignment.agency?.name)
    const agency = detail ? { id: sources.agencies?.find((item) => (item.agencyName || item.name || '').toLowerCase() === detail.toLowerCase())?.id ?? '', name: detail } : null
    const owner = sources.candidate?.ownerId ? sources.users.find((item) => item.id === sources.candidate?.ownerId) : undefined
    next = {
      ...assignment,
      client: client ? { id: client.id, name: client.name, companyId: company?.id ?? '', companyName: company?.name ?? '' } : assignment.client,
      job: order ? {
        orderId: order.id,
        ref: order.ref,
        title: designation?.title || assignment.job?.title || '',
        applicationId: assignment.job?.applicationId ?? '',
        stage: assignment.job?.stage ?? 0,
        country: order.country || assignment.job?.country || '',
      } : assignment.job,
      source: { key: sourceKey, label: sourceKey ? sourceLabel(sourceKey, assignment.source.label) : '' },
      agency,
      recruiter: owner ? { id: owner.id, name: owner.name } : assignment.recruiter,
    }
  }

  const options = next.serverOptions ? next.options : {
    clients: fallbackClientOptions(sources, next.client?.id ?? ''),
    agencies: (sources.agencies ?? []).map((item) => ({ id: item.id, name: (item.agencyName || item.name || '').trim() })).filter((item) => item.name),
    recruiters: isManager(sources.me.role)
      ? sources.users
        .filter((item) => (item.active && !item.onLeave && (item.role === 'staff' || item.role === 'manager')) || item.id === next.recruiter?.id)
        .map((item) => ({ id: item.id, name: item.name, role: item.role }))
        .sort((a, b) => a.name.localeCompare(b.name))
      : [],
  }
  return { ...next, options, can: next.serverCan ? next.can : fallbackCan(sources.me, sources.canEdit) }
}
