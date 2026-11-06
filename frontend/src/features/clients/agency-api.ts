import { api } from '@/shared/api/client'
import type { ReferenceAgency } from '@/shared/model/types'
import { useStore } from '@/shared/state/crm-store'
import { useReportData } from '@/features/reports/report-api'
import type { buildAgencyPayload } from '@/features/clients/client-form'

type AgencyPayload = ReturnType<typeof buildAgencyPayload>

/** The server announces changes to this table on this live topic, so every open screen re-reads after a save or delete. */
export const AGENCY_TOPICS: readonly string[] = ['reference_agencies']

export const agencyApi = {
  create: (payload: AgencyPayload) => api.post<ReferenceAgency>('/reference-agencies', payload),
  update: (id: string, payload: AgencyPayload) => api.put<ReferenceAgency>('/reference-agencies/' + encodeURIComponent(id), payload),
  remove: (id: string) => api.del<void>('/reference-agencies/' + encodeURIComponent(id)),
}

/**
 * The reference agencies, newest first, kept live. `enabled: false` reads nothing (the Clients side of the page, or an
 * Add candidate form that only needs the names for a referral). Call `refresh()` after a save or delete for an instant update.
 */
export function useReferenceAgencies(enabled = true) {
  const store = useStore()
  const result = useReportData<{ items: ReferenceAgency[] }>(enabled ? '/reference-agencies' : null, store.me.id, '', AGENCY_TOPICS)
  return { items: result.data?.items ?? null, error: result.error, refreshing: result.refreshing, refresh: result.refresh }
}
