import type { CSSProperties } from 'react'
import { Globe2 } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { initials } from '@/shared/lib/format'
import { STAGES, TOTAL_STAGES } from '@/shared/workflow/stages'

/** Care India's own touches for the jobs screens: per-name avatars, country flags and the 11-stage tracker. */

const HUES = [222, 268, 160, 28, 340, 190]
export const hueOf = (name: string) => HUES[[...name].reduce((total, character) => total + character.charCodeAt(0), 0) % HUES.length]

/** A circular avatar whose soft hue is derived from the name, so the same person always looks the same. */
export function Avatar({ name, size = 'md', className }: { name: string; size?: 'sm' | 'md' | 'lg'; className?: string }) {
  return (
    <span className={cn('jv-avatar', size !== 'md' && `is-${size}`, className)} style={{ '--h': hueOf(name || '?') } as CSSProperties} aria-hidden="true">
      {initials(name || '?')}
    </span>
  )
}

const FLAGS: [string, string[]][] = [
  ['ae', ['united arab emirates', 'uae', 'emirates', 'dubai', 'abu dhabi', 'sharjah']],
  ['sa', ['saudi']],
  ['qa', ['qatar']],
  ['kw', ['kuwait']],
  ['om', ['oman']],
  ['bh', ['bahrain']],
  ['de', ['germany']],
  ['gb', ['united kingdom', 'uk', 'britain', 'england']],
  ['in', ['india']],
  ['sg', ['singapore']],
  ['us', ['united states', 'usa', 'america']],
]

export const countryCode = (country?: string | null) => {
  const key = (country ?? '').trim().toLowerCase()
  if (!key) return null
  return FLAGS.find(([, names]) => names.some((name) => key === name || key.includes(name)))?.[0] ?? null
}

/** The country's flag from /flags, or a globe when it is not one we ship (or none is recorded). */
export function Flag({ country, className }: { country?: string | null; className?: string }) {
  const code = countryCode(country)
  return code
    ? <img src={`/flags/${code}.svg`} alt="" width={20} height={14} className={cn('jv-flag-img', className)} loading="lazy" />
    : <Globe2 size={15} aria-hidden="true" className={cn('jv-flag-globe', className)} />
}

/** Work location with its flag: "Dubai, United Arab Emirates". */
export function Place({ city, country, empty = 'Place not added' }: { city?: string | null; country?: string | null; empty?: string }) {
  const text = [city, country].filter(Boolean).join(', ')
  return <span className="jv-place"><Flag country={country} />{text || <i>{empty}</i>}</span>
}

/**
 * Where an application stands among the eleven stages: one segment per stage, the stages passed
 * filled, the current one marked, with its number and name beside it.
 */
export function StageMini({ no, closed }: { no: number; closed?: boolean }) {
  const stage = STAGES[Math.max(0, Math.min(TOTAL_STAGES, no) - 1)]
  return (
    <span className={cn('jv-tracker', closed && 'is-stopped')} title={`Stage ${no} of ${TOTAL_STAGES}: ${stage.title}`}>
      <span className="jv-tracker-bar" aria-hidden="true">
        {STAGES.map((item) => <i key={item.no} className={cn(item.no < no && 'is-done', item.no === no && 'is-now')} />)}
      </span>
      <span className="jv-tracker-label"><b>{no}</b>{stage.title}</span>
    </span>
  )
}
