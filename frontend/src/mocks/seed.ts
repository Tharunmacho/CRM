/**
 * Historical frontend fixtures retained separately from live API data.
 *
 * Live records, users, and authentication come from shared/state/crm-store.tsx.
 * The Users audit tab still reads the empty auditLog below until its backend
 * integration is implemented.
 *
 * Agency presentation settings live in app/config/agency.ts.
 */
import type {
  Application,
  AuditEntry,
  Candidate,
  Client,
  Company,
  Designation,
  IngestionFailure,
  MailAccount,
  MailMessage,
  Notification,
  Order,
  Remark,
  Task,
  TodoItem,
} from '@/shared/model/types'

export const clients: Client[] = []
export const companies: Company[] = []
export const designations: Designation[] = []
export const orders: Order[] = []
export const candidates: Candidate[] = []
export const applications: Application[] = []
export const tasks: Task[] = []
export const remarks: Remark[] = []
export const todos: TodoItem[] = []
export const mailAccounts: MailAccount[] = []
export const mailMessages: MailMessage[] = []
export const notifications: Notification[] = []
export const ingestionFailures: IngestionFailure[] = []
export const auditLog: AuditEntry[] = []
