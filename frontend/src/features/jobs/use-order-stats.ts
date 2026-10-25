import { useMemo } from 'react'
import { useStore } from '@/shared/state/crm-store'
import { orderCounts, type OrderCounts } from '@/features/jobs/order-counts'

export interface OrderStats extends OrderCounts { closed: number; total: number }
const NONE: OrderStats = { matched: 0, selected: 0, deployed: 0, closed: 0, total: 0 }

/**
 * One pass over the applications gives every order its numbers (matched, selected, deployed, closed),
 * so lists and cards do not each re-scan the whole store for every row.
 */
export function useOrderStats() {
  const store = useStore()
  return useMemo(() => {
    const byOrder = new Map<string, typeof store.applications>()
    for (const application of store.applications) {
      const list = byOrder.get(application.orderId)
      if (list) list.push(application); else byOrder.set(application.orderId, [application])
    }
    const stats = new Map<string, OrderStats>()
    for (const [orderId, list] of byOrder) {
      stats.set(orderId, { ...orderCounts(list), closed: list.filter((application) => application.status === 'closed').length, total: list.length })
    }
    return { of: (orderId: string): OrderStats => stats.get(orderId) ?? NONE }
  }, [store.applications]) // eslint-disable-line react-hooks/exhaustive-deps
}

/** How many of an order's openings are deployed, in the stages, and still unmatched (never negative). */
export function seatSplit(openings: number, deployed: number, matched: number) {
  const done = Math.max(0, Math.min(openings, deployed))
  const inStages = Math.max(0, Math.min(matched, openings - done))
  return { done, inStages, free: Math.max(0, openings - done - inStages) }
}
