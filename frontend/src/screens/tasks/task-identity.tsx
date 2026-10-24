import { Check, Globe2, PhoneCall } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { initials } from '@/shared/lib/format'
import { STAGES, TOTAL_STAGES } from '@/shared/workflow/stages'

/** Care India's own touches on the task list: country flags, the stage tracker, the owner tag and the 48-hour ring. */

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

/** The country's flag from /flags (matched by name), or a globe when we do not ship it or none is recorded. */
export function Flag({ country }: { country?: string | null }) {
  const key = (country ?? '').trim().toLowerCase()
  const code = key ? FLAGS.find(([, names]) => names.some((name) => key === name || key.includes(name)))?.[0] : undefined
  return code
    ? <img src={`/flags/${code}.svg`} alt="" width={20} height={14} className="tk-flag" loading="lazy" />
    : <Globe2 size={15} aria-hidden="true" className="tk-flag-globe" />
}

/** Owner as a circular hue avatar plus first name; the full name is the tooltip. */
export function OwnerTag({ name }: { name?: string }) {
  if (!name) return <span className="tk-muted">Unassigned</span>
  const hue = [...name].reduce((total, character) => total + character.charCodeAt(0), 0) % 6
  return (
    <span className="tk-owner" title={name}>
      <span className={cn('record-avatar tk-owner-avatar', `avatar-hue-${hue}`)} aria-hidden="true">{initials(name)}</span>
      <span className="tk-owner-name">{name.split(' ')[0]}</span>
    </span>
  )
}

/** The 11-stage tracker: one segment per stage, passed stages filled, the current one marked. */
export function StageTrack({ no }: { no?: number }) {
  if (!no) {
    return (
      <span className="tk-track is-call" title="Initial call, before a stage starts">
        <span className="tk-track-bar" aria-hidden="true">{STAGES.map((item) => <i key={item.no} />)}</span>
        <span className="tk-track-label"><PhoneCall size={12} aria-hidden="true" />Initial call</span>
      </span>
    )
  }
  const stage = STAGES[Math.max(0, Math.min(TOTAL_STAGES, no) - 1)]
  return (
    <span className="tk-track" title={`Stage ${no} of ${TOTAL_STAGES}: ${stage.title}`}>
      <span className="tk-track-bar" aria-hidden="true">
        {STAGES.map((item) => <i key={item.no} className={cn(item.no < no && 'is-done', item.no === no && 'is-now')} />)}
      </span>
      <span className="tk-track-label"><b>{no}</b>{stage.title}</span>
    </span>
  )
}

/** The 48-hour ring: the share of the window already used, coloured by where the task stands. */
export function SlaRing({ fill, tone, label }: { fill: number; tone: 'ok' | 'soon' | 'over' | 'esc' | 'done'; label: string }) {
  const radius = 11
  const circumference = 2 * Math.PI * radius
  return (
    <span className={cn('tk-ring', `is-${tone}`)} role="img" aria-label={label}>
      <svg viewBox="0 0 28 28" width="28" height="28" aria-hidden="true">
        <circle className="tk-ring-track" cx="14" cy="14" r={radius} fill="none" strokeWidth="3" />
        <circle
          className="tk-ring-fill"
          cx="14" cy="14" r={radius} fill="none" strokeWidth="3" strokeLinecap="round"
          strokeDasharray={`${Math.max(0.04, Math.min(1, fill)) * circumference} ${circumference}`}
          transform="rotate(-90 14 14)"
        />
      </svg>
      {tone === 'done' && <Check size={12} strokeWidth={2.6} aria-hidden="true" />}
    </span>
  )
}
