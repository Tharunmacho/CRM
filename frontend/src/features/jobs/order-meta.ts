import type { Order } from '@/shared/model/types'

/** Presentation helpers shared by the jobs list, the order cards and the order page. */
export type OrderStatusKey = 'open' | 'on_hold' | 'filled' | 'closed' | 'overdue'

export const ORDER_STATUS_TEXT: Record<OrderStatusKey, string> = {
  open: 'Open',
  on_hold: 'On hold',
  filled: 'Filled',
  closed: 'Closed',
  overdue: 'Overdue',
}

const DAY = 86_400_000
const dayStart = (value: string | Date) => {
  const date = typeof value === 'string' ? new Date(value.length === 10 ? `${value}T00:00:00` : value) : value
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
}

/** Whole days from today to the deadline: negative once it has passed. */
export function daysToDeadline(deadline: string | null | undefined, now: Date = new Date()): number | null {
  if (!deadline) return null
  return Math.round((dayStart(deadline) - dayStart(now)) / DAY)
}

export const isOverdue = (order: Pick<Order, 'status' | 'deadline'>) => {
  const days = daysToDeadline(order.deadline)
  return order.status === 'open' && days !== null && days < 0
}

export const orderStatusKey = (order: Pick<Order, 'status' | 'deadline'>): OrderStatusKey => (isOverdue(order) ? 'overdue' : order.status)

/** "Due in 5 days", "Due today", "3 days overdue": empty when there is no deadline. */
export function deadlineNote(order: Pick<Order, 'status' | 'deadline'>): string {
  const days = daysToDeadline(order.deadline)
  if (days === null) return ''
  if (order.status === 'filled' || order.status === 'closed') return ''
  if (days < 0) return `${-days} ${days === -1 ? 'day' : 'days'} overdue`
  if (days === 0) return 'Due today'
  return `Due in ${days} ${days === 1 ? 'day' : 'days'}`
}

/** Moves a deadline forward from the later of today and its current date (as the reference product does). */
export function extendedDeadline(deadline: string | null | undefined, days: number, now: Date = new Date()): string {
  const today = new Date(dayStart(now))
  const current = deadline ? new Date(dayStart(deadline)) : null
  const base = current && current.getTime() > today.getTime() ? current : today
  base.setDate(base.getDate() + days)
  return `${base.getFullYear()}-${String(base.getMonth() + 1).padStart(2, '0')}-${String(base.getDate()).padStart(2, '0')}`
}

/** The payload the order endpoint expects, built from a saved order with some fields changed. */
export function orderPayload(order: Order, changes: Partial<Pick<Order, 'status' | 'deadline'>> = {}) {
  return {
    companyId: order.companyId,
    designationId: order.designationId,
    openings: order.openings,
    country: order.country ?? '',
    city: order.city ?? '',
    salary: order.salary,
    currency: order.currency,
    benefits: order.benefits,
    contractPeriod: order.contractPeriod ?? '',
    interviewDate: order.interviewDate || null,
    deadline: order.deadline || null,
    status: order.status,
    ...changes,
  }
}

const csvCell = (value: unknown) => {
  const text = value == null ? '' : String(value)
  // Leading spreadsheet formula characters are neutralised so an exported cell cannot run as one.
  const safe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text
  return /[",\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe
}

export function toCsv(header: string[], rows: unknown[][]): string {
  return [header, ...rows].map((row) => row.map(csvCell).join(',')).join('\r\n')
}
