/**
 * The candidate's Documents card as plain data: the shared slot list (labels and order from `document-slots.ts`) filled with what
 * the server holds. No React, so it can be tested directly.
 *
 * The server sends `documents` as `{ key, label, versions }`. Older records keep certificates under the earlier slot keys
 * (`education`, `experience`); those files are shown in the matching new row so nothing disappears. Slots that are not in the
 * shared list (Aadhaar, the stored source document) keep their server label and come after it.
 */
import { DOCUMENT_SLOTS } from '@/shared/model/document-slots'
import type { DocumentSlot as ServerSlot, FileVersion } from '@/shared/model/types'

export interface ProfileDocument {
  key: string
  label: string
  versions: FileVersion[]
  /** Several files are kept side by side (Other documents); every other slot keeps one current file plus its history. */
  multiple: boolean
  manualStatus?: ServerSlot['manualStatus']
}

/** Earlier slot keys and the shared slot that now holds their files. */
export const LEGACY_SLOT_KEYS: Record<string, string> = { education: 'education_certificate', experience: 'experience_certificate' }

/** Not in the shared list, but always offered on the profile (as before). */
const ALWAYS_LISTED: Record<string, string> = { aadhaar: 'Aadhaar copy' }

/**
 * Two histories of one slot as one: oldest first, renumbered 1..n, so "newest" and "vN" stay true across the old and the new key.
 * A single history is returned as it is.
 */
export function mergeVersions(first: FileVersion[], second: FileVersion[]): FileVersion[] {
  if (first.length === 0) return second
  if (second.length === 0) return first
  return [...first, ...second]
    .sort((a, b) => String(a.uploadedAt).localeCompare(String(b.uploadedAt)) || a.version - b.version)
    .map((file, index) => ({ ...file, version: index + 1 }))
}

/** The 14 shared slots in order (empty ones included), then Aadhaar, then any other slot that holds a file. */
export function profileDocuments(documents: ServerSlot[] | undefined, options: { invoices?: boolean } = {}): ProfileDocument[] {
  // The Invoice document is the admin's: everyone else gets no Invoice row, empty or not.
  const showInvoice = options.invoices !== false
  const held = new Map<string, FileVersion[]>()
  const labels = new Map<string, string>()
  const manual = new Map<string, ServerSlot['manualStatus']>()
  for (const slot of documents ?? []) {
    const key = LEGACY_SLOT_KEYS[slot.key] ?? slot.key
    held.set(key, mergeVersions(held.get(key) ?? [], slot.versions ?? []))
    if (slot.manualStatus) manual.set(key, slot.manualStatus)
    if (!(slot.key in LEGACY_SLOT_KEYS)) labels.set(key, slot.label)
  }
  const shared = DOCUMENT_SLOTS.map((entry) => ({ key: entry.slot, label: entry.label, versions: held.get(entry.slot) ?? [], multiple: entry.multiple, manualStatus: manual.get(entry.slot) }))
    .filter((slot) => slot.key !== 'application_form' || slot.versions.length > 0 || slot.manualStatus?.uploaded)
    .filter((slot) => showInvoice || slot.key !== 'invoice')
  const sharedKeys = new Set(DOCUMENT_SLOTS.map((entry) => entry.slot))
  const extras: ProfileDocument[] = []
  for (const [key, versions] of held) {
    if (sharedKeys.has(key)) continue
    if (versions.length === 0 && !(key in ALWAYS_LISTED)) continue
    extras.push({ key, label: labels.get(key) || ALWAYS_LISTED[key] || key, versions, multiple: false, manualStatus: manual.get(key) })
  }
  for (const [key, label] of Object.entries(ALWAYS_LISTED)) {
    if (!held.has(key) && !sharedKeys.has(key)) extras.push({ key, label, versions: [], multiple: false })
  }
  return [...shared, ...extras]
}
