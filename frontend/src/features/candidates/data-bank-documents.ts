/**
 * The "Documents on file" value of a Data Bank row: the labels of the slots that hold a file, in the shared slot order
 * (`document-slots.ts`). The server sends `{ key, label, versions }` per slot; its own labels are not used, so the Data Bank
 * says what the application form and the profile say. Older records keep certificates under the earlier keys (`education`,
 * `experience`); those count as the matching certificate. A slot outside the shared list keeps a readable name after the rest.
 */
import { DOCUMENT_SLOT_KEYS, documentLabel } from '@/shared/model/document-slots'

const LEGACY_KEYS: Record<string, string> = { education: 'education_certificate', experience: 'experience_certificate' }

export interface HeldSlot {
  key: string
  versions?: unknown[] | null
}

export function documentsOnFile(documents: HeldSlot[] | null | undefined): string[] {
  const held = new Set<string>()
  for (const slot of documents ?? []) {
    if ((slot.versions?.length ?? 0) > 0) held.add(LEGACY_KEYS[slot.key] ?? slot.key)
  }
  const shared = DOCUMENT_SLOT_KEYS.filter((key) => held.has(key))
  const extra = [...held].filter((key) => !DOCUMENT_SLOT_KEYS.includes(key))
  return [...shared, ...extra].map(documentLabel)
}
