import type { Application, Candidate, Client, Company, Designation, Order, Remark, Task, User } from '@/shared/model/types'

interface Records {
  candidates: Candidate[]; clients: Client[]; companies: Company[]; designations: Designation[]
  orders: Order[]; users: User[]; applications: Application[]; tasks: Task[]; remarks: Remark[]
}

const keyed = <T extends { id: string }>(rows: T[]) => new Map(rows.map(row => [row.id, row]))

/** Build once per changed collection instead of scanning the desk in every table cell. */
export function indexWorkspace(data: Records) {
  const applications = new Map<string, Application[]>()
  const activeApplications = new Map<string, Application>()
  for (const row of data.applications) {
    const group = applications.get(row.candidateId) ?? []
    group.push(row); applications.set(row.candidateId, group)
    if (row.status === 'active') activeApplications.set(row.candidateId, row)
  }
  const remarks = new Map<string, Remark[]>()
  for (const row of data.remarks) {
    const group = remarks.get(row.taskId) ?? []
    group.push(row); remarks.set(row.taskId, group)
  }
  for (const group of remarks.values()) group.sort((a, b) => +new Date(b.createdAt) - +new Date(a.createdAt))
  return {
    candidates: keyed(data.candidates), clients: keyed(data.clients), companies: keyed(data.companies),
    designations: keyed(data.designations), orders: keyed(data.orders), users: keyed(data.users),
    tasks: keyed(data.tasks), applications, activeApplications, remarks,
  }
}
