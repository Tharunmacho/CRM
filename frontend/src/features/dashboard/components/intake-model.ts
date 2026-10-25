import type { ArrivalDay, BucketUnit, IntakeBucket } from '@/features/dashboard/metrics'

export const number = (value: number) => value.toLocaleString()
export const percentOf = (part: number, total: number) => (total ? Math.round(part / total * 100) : 0)
export const plural = (count: number, one: string, many = `${one}s`) => `${number(count)} ${count === 1 ? one : many}`

/** The five ways a candidate can arrive. The colour is a chart token and the name is always written beside it. */
export const INTAKE_SOURCES = [
  { key: 'email', label: 'Email' }, { key: 'whatsapp', label: 'WhatsApp' }, { key: 'walk_in', label: 'Walk-in' },
  { key: 'referral', label: 'Referral' }, { key: 'other', label: 'Other' },
] as const
export const SOURCE_KEYS: string[] = INTAKE_SOURCES.map(source => source.key)
export const colourOf = (key: string) => `var(--dc-${key})`

export interface RecentRow { id: string; name: string; source: string; addedAt: string; status: string; role: string }

export interface IntakeOverview {
  total: number
  verified: number
  unit: BucketUnit
  buckets: IntakeBucket[]
  /** Candidates per source in the chosen period / in the period of the same length before it / of those, how many reached Deployed. */
  sources: Record<string, number>
  previous: Record<string, number>
  placed: Record<string, number>
  /** Newest first, at most a dozen. */
  recent: RecentRow[]
  /** Day columns for the last 7, 14 and 30 days, inside the chosen period. */
  daily: Record<number, IntakeBucket[]>
  /** The chosen period in days, or null when it is open-ended. */
  span: number | null
}
export interface StagePerson { id: string; name: string; days: number | null }
export interface StageRow { stage: number; label: string; count: number; people: StagePerson[] }
export interface Arrivals { columns: ArrivalDay[][]; max: number; total: number; activeDays: number; elapsed: number; busiest: ArrivalDay | null }
