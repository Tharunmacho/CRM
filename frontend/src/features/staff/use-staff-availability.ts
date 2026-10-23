import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import type { StaffAvailabilityResult } from './availability-model'

/** Fresh for every dialog and refresh; never fall back to cached manual-leave flags. */
export function useStaffAvailability({ enabled = true, revision = '' }: { enabled?: boolean; revision?: string } = {}) {
  const { me } = useStore()
  const [reload, setReload] = useState(0)
  const key = `${me.id}:${revision}:${reload}`
  const [snapshot, setSnapshot] = useState<{ key: string; data: StaffAvailabilityResult } | null>(null)
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null)
  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    void api.get<StaffAvailabilityResult>('/staff-directory/availability', { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) { setSnapshot({ key, data }); setFailure(null) } })
      .catch(error => { if (!controller.signal.aborted) setFailure({ key, message: error instanceof Error ? error.message : 'Availability could not be checked.' }) })
    return () => controller.abort()
  }, [enabled, key])
  const data = enabled && snapshot?.key === key ? snapshot.data : null
  const error = enabled && failure?.key === key ? failure.message : ''
  return { data, error, loading: enabled && !data && !error, refresh: () => setReload(value => value + 1) }
}
