/**
 * What a person has typed in a candidate's NOTES box but not saved yet, kept per candidate so it is still there when the table is drawn
 * again (a live refresh, a filter, another tab and back, a page reload). Memory first, the browser's session storage as a backup; a note
 * leaves the store when it is saved or cleared. Nothing here ever reaches the server.
 */
const STORAGE_KEY = 'crm.note-drafts.v1'
const MAX_DRAFTS = 200

let drafts: Map<string, string> | null = null

function load(): Map<string, string> {
  if (drafts) return drafts
  drafts = new Map()
  try {
    const raw = typeof sessionStorage === 'undefined' ? null : sessionStorage.getItem(STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : null
    if (parsed && typeof parsed === 'object') for (const [key, value] of Object.entries(parsed)) if (typeof value === 'string' && value) drafts.set(key, value)
  } catch { /* an unreadable store is an empty one */ }
  return drafts
}

function save() {
  try {
    if (typeof sessionStorage === 'undefined' || !drafts) return
    if (drafts.size === 0) sessionStorage.removeItem(STORAGE_KEY)
    else sessionStorage.setItem(STORAGE_KEY, JSON.stringify(Object.fromEntries(drafts)))
  } catch { /* storage may be full or blocked: the in-memory copy still works */ }
}

/** One store key: the writer and the candidate, so a shared computer never shows one person's draft to another. */
export const noteDraftKey = (author: string | undefined, candidateId: string): string => `${author ?? ''}|${candidateId}`

export function getNoteDraft(key: string): string {
  return load().get(key) ?? ''
}

/** Keep `text` as the draft of `key`; an empty (or blank) text removes it. */
export function setNoteDraft(key: string, text: string): void {
  const store = load()
  if (!text.trim()) store.delete(key)
  else {
    store.delete(key)            // re-insert: the newest draft is the last one, so the oldest go first when the store is full
    store.set(key, text)
    while (store.size > MAX_DRAFTS) store.delete(store.keys().next().value as string)
  }
  save()
}

/** For tests: forget everything held in memory (and in storage). */
export function resetNoteDrafts(): void {
  drafts = new Map()
  save()
}
