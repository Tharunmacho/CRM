/**
 * The section navigator of the application form, as plain data (no React): which sections the page has, which of them are
 * available to this person, how a deep link (`#documents`) or a "Still missing" item finds its section, and which of the four
 * detail cards are on screen. The page shows ONE section at a time under a sticky tab bar, so a recruiter never scrolls through
 * the whole form; on a wide screen the sections that belong together (Personal and Passport, Education and Experience) are shown
 * together, so the first screen already holds both.
 */
import type { MissingItem } from './types'

export type FormTab = 'overview' | 'personal' | 'passport' | 'education' | 'experience' | 'documents' | 'invoice' | 'notes'

export interface FormTabDef {
  id: FormTab
  label: string
  /** The element id of the section on the page, kept so older links (`#af-documents`) still work. */
  anchor: string
}

export const FORM_TABS: FormTabDef[] = [
  { id: 'overview', label: 'Overview', anchor: 'af-assignment' },
  { id: 'personal', label: 'Personal', anchor: 'af-fill-section-1' },
  { id: 'passport', label: 'Passport', anchor: 'af-fill-section-2' },
  { id: 'education', label: 'Education', anchor: 'af-fill-section-3' },
  { id: 'experience', label: 'Experience', anchor: 'af-fill-section-4' },
  { id: 'documents', label: 'Documents', anchor: 'af-documents' },
  { id: 'invoice', label: 'Invoice', anchor: 'af-invoice' },
  { id: 'notes', label: 'Notes', anchor: 'af-notes' },
]

/** People start on the personal and passport details: that is the work. */
export const DEFAULT_TAB: FormTab = 'personal'

/** Invoice and Notes exist only for people the server lets see them. */
export function availableTabs(allowed: { invoice: boolean; notes: boolean }): FormTab[] {
  return FORM_TABS.map((tab) => tab.id).filter((id) => (id === 'invoice' ? allowed.invoice : id === 'notes' ? allowed.notes : true))
}

/** Names a link may use besides the tab id and the element id. */
const HASH_ALIASES: Record<string, FormTab> = {
  client: 'overview', assignment: 'overview', stages: 'overview', process: 'overview', 'af-process': 'overview',
  details: 'personal', photo: 'personal', recruiter: 'overview',
  docs: 'documents', files: 'documents',
  note: 'notes', 'recruiter-notes': 'notes',
}

/** The tab a link points at: `#documents`, `#af-documents`, `#passport`, `#af-fill-section-3`. Anything else is nothing. */
export function tabFromHash(hash: string | null | undefined): FormTab | null {
  let name = (hash ?? '').replace(/^#/, '')
  try { name = decodeURIComponent(name) } catch { /* A malformed link is read as typed. */ }
  name = name.trim().toLowerCase()
  if (!name) return null
  const direct = FORM_TABS.find((tab) => tab.id === name || tab.anchor === name)
  return direct?.id ?? HASH_ALIASES[name] ?? null
}

export const hashForTab = (tab: FormTab): string => `#${tab}`

/** Where each numbered section of the paper form lives: 1 Personal, 2 Passport, 3 Education, 4 Experience, 5 Documents, 6 and 7 Overview. */
const SECTION_TABS: Record<number, FormTab> = { 1: 'personal', 2: 'passport', 3: 'education', 4: 'experience', 5: 'documents', 6: 'overview', 7: 'overview' }
export const tabForSection = (section: number): FormTab => SECTION_TABS[section] ?? DEFAULT_TAB

/** The tab of a "Still missing" item: by the field it names, falling back to its section number. */
export function tabForMissing(item: Pick<MissingItem, 'key' | 'kind' | 'section'>): FormTab {
  if (item.kind === 'document' || item.key.startsWith('documents.')) return 'documents'
  if (item.key.startsWith('personal.')) return 'personal'
  if (item.key.startsWith('passport.')) return 'passport'
  if (item.key === 'education') return 'education'
  if (item.key.startsWith('experience')) return 'experience'
  return tabForSection(item.section)
}

/** How many missing items each tab holds (only the tabs that hold any appear). */
export function missingByTab(items: Pick<MissingItem, 'key' | 'kind' | 'section'>[]): Partial<Record<FormTab, number>> {
  const out: Partial<Record<FormTab, number>> = {}
  for (const item of items) { const tab = tabForMissing(item); out[tab] = (out[tab] ?? 0) + 1 }
  return out
}

/** The detail sections are the paper form's numbered cards: 1 Personal, 2 Passport, 3 Education, 4 Experience. */
export type DetailCard = 1 | 2 | 3 | 4
export const DETAIL_TABS: FormTab[] = ['personal', 'passport', 'education', 'experience']
export const isDetailTab = (tab: FormTab): boolean => DETAIL_TABS.includes(tab)

/** The id of the panel a tab controls: the four detail tabs share one panel. */
export const paneIdFor = (tab: FormTab): string => (isDetailTab(tab) ? 'af-pane-details' : `af-pane-${tab}`)

/**
 * Which of the four detail cards is on screen: one card per tab, on every screen width. Personal, Passport, Education and Experience
 * are each their own tab, so the tab bar has exactly one active state and a card is never squeezed beside another.
 */
export function visibleCards(tab: FormTab): DetailCard[] {
  switch (tab) {
    case 'personal': return [1]
    case 'passport': return [2]
    case 'education': return [3]
    case 'experience': return [4]
    default: return []
  }
}

/** The tab the arrow keys, Home and End move to (the tab bar is one tab stop; arrows move inside it). */
export function nextTab(tabs: FormTab[], current: FormTab, key: string): FormTab | null {
  if (!tabs.length) return null
  const at = Math.max(0, tabs.indexOf(current))
  if (key === 'ArrowRight' || key === 'ArrowDown') return tabs[(at + 1) % tabs.length]
  if (key === 'ArrowLeft' || key === 'ArrowUp') return tabs[(at - 1 + tabs.length) % tabs.length]
  if (key === 'Home') return tabs[0]
  if (key === 'End') return tabs[tabs.length - 1]
  return null
}

/** The label of a tab with its small count, for the screen reader ("Documents, 3 missing"). */
export function tabAccessibleName(label: string, badge: { count: number; kind: 'missing' | 'notes' | 'overdue' | 'done' } | null): string {
  if (badge?.kind === 'done') return `${label}, complete`
  if (!badge || (badge.count <= 0 && badge.kind !== 'overdue')) return label
  if (badge.kind === 'overdue') return `${label}, a note is overdue`
  return badge.kind === 'notes' ? `${label}, ${badge.count} ${badge.count === 1 ? 'note' : 'notes'}` : `${label}, ${badge.count} missing`
}
