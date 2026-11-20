/**
 * One candidate's document files, for the few screens that show or reuse the files themselves.
 *
 * The workspace snapshot lists candidates without their files (`documentsSummary`, see shared/state/desk-wire.ts):
 * the files of every candidate would be half of what each page load downloads. A screen about ONE candidate (the
 * stage window, the profile) reads that candidate when it opens, here, and again when the snapshot says the candidate's
 * files changed or the server announces a change to documents. Until the read arrives the summary is returned with
 * `loading: true`, so a screen can wait instead of showing "no files". A candidate that already carries its files
 * (a profile read, an older server) is returned as it is, with no request.
 */
import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import { live } from '@/shared/state/live'
import type { Candidate, DocumentSlot } from '@/shared/model/types'

/** The slots the workspace snapshot has always listed; a profile read also carries the application form's other slots. */
const WORKSPACE_SLOTS = ['resume', 'passport', 'aadhaar', 'source_bundle', 'photo', 'education', 'experience']

export function useCandidateDocuments(candidate: Candidate | null | undefined): { documents: DocumentSlot[]; loading: boolean } {
  const id = candidate?.id
  const summary = candidate?.documentsSummary === true
  const signature = JSON.stringify(candidate?.documentCounts ?? {})
  const [loaded, setLoaded] = useState<{ id: string; documents: DocumentSlot[] } | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [announced, setAnnounced] = useState(0)

  useEffect(() => {
    if (!id || !summary) return
    const controller = new AbortController()
    api.get<Candidate>(`/candidates/${id}`, { signal: controller.signal })
      .then((found) => {
        if (controller.signal.aborted) return
        setFailed(null)
        setLoaded({ id, documents: (found.documents ?? []).filter((slot) => WORKSPACE_SLOTS.includes(slot.key)) })
      })
      .catch(() => { if (!controller.signal.aborted) setFailed(id) }) // keep what is shown; a later change retries
    return () => controller.abort()
  }, [id, summary, signature, announced])

  useEffect(() => {
    if (!id || !summary) return
    return live.subscribe(['documents', 'candidates'], () => setAnnounced((n) => n + 1), { debounceMs: 400 })
  }, [id, summary])

  if (!candidate) return { documents: [], loading: false }
  if (!summary) return { documents: candidate.documents, loading: false }
  if (loaded && loaded.id === id) return { documents: loaded.documents, loading: false }
  return { documents: candidate.documents, loading: failed !== id }
}
