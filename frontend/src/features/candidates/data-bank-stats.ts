/**
 * The Data Bank's three stat cards and the filters shown beside the search, as plain functions (no React), so they are unit-tested.
 *
 * Cards: Resumes (everyone on file), Not assigned (no recruiter yet: the pool an admin or manager assigns by hand, so staff never see it)
 * and Needs review. Each card is also a filter: pressing it applies its pool, pressing the active one again returns to everyone.
 * The other pools (No order yet, Not interested, Deployed...) stay available as filters, not as cards.
 */
import { NOT_ASSIGNED_LABEL, togglePool, type DataBankPool, type PoolCounts } from './data-bank-pools'

export type StatKey = 'resumes' | 'unassigned' | 'needs_review'
export type StatTone = 'blue' | 'amber' | 'violet'

export interface StatCard {
  key: StatKey
  label: string
  value: number
  pool: DataBankPool
  tone: StatTone
  /** Nothing to act on (a zero that is not the active filter): the card stays but reads quieter. */
  muted: boolean
  active: boolean
}

/** Admin and manager hand candidates to recruiters; staff only see their own work. */
export const isSupervisor = (role: string | null | undefined): boolean => role === 'admin' || role === 'manager'

export function statCards(counts: Pick<PoolCounts, 'all' | 'unassigned' | 'needs_review'>, pool: DataBankPool, role: string | null | undefined): StatCard[] {
  const cards: StatCard[] = [
    { key: 'resumes', label: 'Resumes', value: counts.all, pool: 'all', tone: 'blue', muted: false, active: pool === 'all' },
  ]
  if (isSupervisor(role)) {
    cards.push({ key: 'unassigned', label: NOT_ASSIGNED_LABEL, value: counts.unassigned, pool: 'unassigned', tone: 'amber', muted: counts.unassigned === 0 && pool !== 'unassigned', active: pool === 'unassigned' })
  }
  cards.push({ key: 'needs_review', label: 'Needs review', value: counts.needs_review, pool: 'needs_review', tone: 'violet', muted: counts.needs_review === 0 && pool !== 'needs_review', active: pool === 'needs_review' })
  return cards
}

/** What pressing a card does to the pool: the Resumes card always shows everyone, the others toggle. */
export const poolAfterCardPress = (current: DataBankPool, card: Pick<StatCard, 'pool'>): DataBankPool =>
  card.pool === 'all' ? 'all' : togglePool(current, card.pool)

export type VisibleFilter = 'scope' | 'source' | 'status' | 'staff'

/** The selects always on show beside the search; Staff is for the people who assign candidates. Everything else lives under More filters. */
export const visibleFilters = (role: string | null | undefined): VisibleFilter[] =>
  isSupervisor(role) ? ['scope', 'source', 'status', 'staff'] : ['scope', 'source', 'status']

/** Behind "More filters": the pool list (No order yet, Not interested...), stage and passport come first, then the text, years, date and skill filters. */
export const MORE_FILTER_SELECTS = ['pool', 'stage', 'passport'] as const

/** "Everyone" or "My candidates": staff is the person's own id, anything else means another staff member was picked in the Staff filter. */
export type ScopeValue = 'everyone' | 'mine' | 'other'
export const scopeValue = (staff: string, meId: string | null | undefined): ScopeValue => (!staff ? 'everyone' : staff === meId ? 'mine' : 'other')
export const staffForScope = (scope: string, meId: string): string => (scope === 'mine' ? meId : '')

/** The pool choices of the More filters list; Not assigned is only offered to those who can assign (or while it is the active filter). */
export function poolChoices(role: string | null | undefined, current: DataBankPool): [DataBankPool, string][] {
  const all: [DataBankPool, string][] = [['all', 'Everyone'], ['unmapped', 'No order yet'], ['unassigned', NOT_ASSIGNED_LABEL], ['needs_review', 'Needs review'], ['not_interested', 'Not interested']]
  return all.filter(([value]) => value !== 'unassigned' || isSupervisor(role) || current === 'unassigned')
}
