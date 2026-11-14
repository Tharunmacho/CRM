import { useState, useSyncExternalStore } from 'react'
import { Check, HandHelping } from 'lucide-react'
import { api } from '@/shared/api/client'
import type { Candidate } from '@/shared/model/types'
import type { useStore } from '@/shared/state/crm-store'
import {
  REQUESTED_LABEL, REQUEST_LABEL, canRequestAllocation, hasRequested, markRequested, requestAllocationPath, requestMessage, requestTitle,
  requestedVersion, subscribeRequested, type AllocationRequestResult,
} from '@/features/candidates/request-allocation'

type Store = ReturnType<typeof useStore>

/**
 * "Request" beside the application-form button, for a staff member looking at a candidate who is not theirs.
 * One click tells every active admin (the server de-duplicates), then the button reads "Requested" and no longer sends.
 * `labelled` is the cards and the expanded row; the table shows the icon alone with the words in its tooltip.
 */
export function RequestAllocationButton({ candidate, store, labelled = false }: { candidate: Candidate; store: Store; labelled?: boolean }) {
  useSyncExternalStore(subscribeRequested, requestedVersion, requestedVersion)
  const [busy, setBusy] = useState(false)
  if (!canRequestAllocation(store.me, candidate)) return null
  const done = hasRequested(store.me.id, candidate.id)
  const send = async () => {
    if (busy || done) return
    setBusy(true)
    try {
      const result = await api.post<AllocationRequestResult>(requestAllocationPath(candidate.id))
      const message = requestMessage(result, candidate.name)
      markRequested(store.me.id, candidate.id)
      store.notify(message.text, message.tone)
    } catch (error) {
      store.notify(error instanceof Error ? error.message : 'We could not send your request. Please try again.', 'warn')
    } finally { setBusy(false) }
  }
  const Icon = done ? Check : HandHelping
  const word = done ? REQUESTED_LABEL : REQUEST_LABEL
  return (
    <button type="button" className={`db-act is-request${labelled ? ' is-label' : ''}${done ? ' is-done' : ''}`} aria-disabled={done || busy ? 'true' : undefined}
      title={requestTitle(candidate.name, done)} aria-label={done ? `Requested ${candidate.name}` : `Request ${candidate.name} from the admin`}
      onClick={() => void send()}><Icon size={labelled ? 14 : 15} aria-hidden="true" />{labelled && <span>{busy ? 'Sending…' : word}</span>}</button>
  )
}
