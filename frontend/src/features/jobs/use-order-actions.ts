import { useCallback, useState } from 'react'
import { api } from '@/shared/api/client'
import type { Order, OrderStatus } from '@/shared/model/types'
import { useStore } from '@/shared/state/crm-store'
import { refreshMasterData } from '@/features/master-data/components/record-audit'
import { extendedDeadline, orderPayload } from '@/features/jobs/order-meta'

/**
 * Quick edits that do not need the full order form: pause or reopen matching, or push the
 * deadline out. They send the same payload the order dialog does, so the server applies the
 * same rules (for example an order whose openings are all deployed cannot be reopened).
 */
export function useOrderActions() {
  const store = useStore()
  const [busyId, setBusyId] = useState<string | null>(null)

  const save = useCallback(async (order: Order, changes: Partial<Pick<Order, 'status' | 'deadline'>>, message: string) => {
    if (busyId) return
    setBusyId(order.id)
    try {
      await api.put<Order>('/orders/' + order.id, orderPayload(order, changes))
      await refreshMasterData(store, message)
    } catch (error) {
      store.notify(error instanceof Error ? error.message : 'We could not update the order. Please try again.', 'warn')
    } finally {
      setBusyId(null)
    }
  }, [busyId, store])

  const setStatus = useCallback((order: Order, status: OrderStatus) => save(order, { status }, status === 'on_hold' ? 'Order is on hold' : 'Order is open again'), [save])
  const extend = useCallback((order: Order, days: number) => save(order, { deadline: extendedDeadline(order.deadline, days) }, `Deadline extended by ${days} days`), [save])

  return { busyId, setStatus, extend }
}
