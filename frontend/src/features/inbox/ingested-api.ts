import { ApiError, api } from '@/shared/api/client'
import { ingestedQuery } from './ingested-view'

/**
 * The "From email" intake log: every email that arrived on a connected mailbox and what became of it.
 * Contract with the backend (GET /api/inbox/ingested and GET /api/inbox/ingested/summary).
 */
export type IngestedStatus = 'ingested' | 'review' | 'failed' | 'ignored' | 'pending'
export type IngestedStatusFilter = 'all' | IngestedStatus

export interface IngestedItem {
  id: string
  messageId: string
  accountId: string
  accountAddress: string
  receivedAt: string
  processedAt: string | null
  from: { name: string | null; address: string }
  subject: string
  attachments: { name: string; sizeKb: number }[]
  attachmentCount: number
  status: IngestedStatus
  candidate: { id: string; ref: string; careId: string; name: string } | null
  /** true: a new candidate was made; false: documents were added to an existing one; null: no candidate. */
  created: boolean | null
  note: string | null
  documentsFiled: number | null
}

export interface IngestedPage {
  items: IngestedItem[]
  total: number
  page: number
  pageSize: number
}

export interface IngestedAccountSummary {
  id: string
  address: string
  provider: string
  connectionStatus: string
  autoImport: boolean
  lastSyncAt: string | null
  lastError: string | null
  ingested: number
  review: number
  failed: number
  ignored: number
  pending: number
}

export interface IngestedSummary {
  ingested: number
  createdCandidates: number
  review: number
  failed: number
  ignored: number
  pending: number
  accounts: IngestedAccountSummary[]
}

export interface IngestedParams {
  /** An account id, or '' / 'all' for every mailbox. */
  account: string
  status: IngestedStatusFilter
  q: string
  /** 0-based. */
  page: number
  pageSize: number
}

export const INGESTED_PAGE_SIZE = 25

export const fetchIngested = (params: IngestedParams, signal?: AbortSignal) =>
  api.get<IngestedPage>(`/inbox/ingested?${ingestedQuery(params)}`, { signal, fresh: true })

export const fetchIngestedSummary = (signal?: AbortSignal) =>
  api.get<IngestedSummary>('/inbox/ingested/summary', { signal, fresh: true })

/** A server that has no intake-log endpoint yet answers 404/405/501, or serves the app shell for unknown paths (502). */
export const ingestedUnavailable = (error: unknown) => error instanceof ApiError && [404, 405, 501, 502].includes(error.status)
