import type { CSSProperties } from 'react'
import { Footprints, Link2, Mail, MessageCircle, UserPlus } from 'lucide-react'
import { initials, slaState } from '@/shared/lib/format'
import { CountryFlag } from '@/shared/ui/country-flag'
import { STAGES, TOTAL_STAGES } from '@/shared/workflow/stages'

/** Care India's own touches on the dashboard: per-name avatars, flags, source icons, the stage tracker and the 48-hour meter. */

const HUES = [222, 268, 160, 28, 340, 190]
export const hueOf = (name: string) => HUES[[...name].reduce((total, character) => total + character.charCodeAt(0), 0) % HUES.length]

export function Avatar({ name, size = 'md' }: { name: string; size?: 'sm' | 'md' }) {
  return <span className={`dr-avatar${size === 'sm' ? ' is-sm' : ''}`} style={{ '--h': hueOf(name || '?') } as CSSProperties} aria-hidden="true">{initials(name || '?')}</span>
}

export function Flag({ country }: { country?: string | null }) {
  return <CountryFlag country={country} className="dr-flag" globeClassName="dr-flag-globe" />
}

export const SOURCE_META: Record<string, { label: string; icon: typeof Mail }> = {
  email: { label: 'Email', icon: Mail }, whatsapp: { label: 'WhatsApp', icon: MessageCircle }, walk_in: { label: 'Walk-in', icon: Footprints },
  referral: { label: 'Referral', icon: UserPlus }, other: { label: 'Other', icon: Link2 },
}
export function SourceTag({ source }: { source: string }) {
  const meta = SOURCE_META[source] ?? SOURCE_META.other
  return <span className={`dr-source dr-tone-${source in SOURCE_META ? source : 'other'}`}><meta.icon size={14} aria-hidden="true" />{meta.label}</span>
}

export function Pill({ tone, children, title }: { tone: 'info' | 'warn' | 'good' | 'bad' | 'muted' | 'accent'; children: string; title?: string }) {
  return <span title={title} className={`dr-pill is-${tone}`}><i />{children}</span>
}

/** One segment per stage: stages passed are filled, the current one is marked. */
export function StageMini({ no }: { no: number | null }) {
  if (!no) return <span className="dr-tracker is-idle"><span className="dr-tracker-bar" aria-hidden="true">{STAGES.map(item => <i key={item.no} />)}</span><span className="dr-tracker-label">Initial call</span></span>
  const stage = STAGES[Math.max(0, Math.min(TOTAL_STAGES, no) - 1)]
  return <span className="dr-tracker" title={`Stage ${no} of ${TOTAL_STAGES}: ${stage.title}`}>
    <span className="dr-tracker-bar" aria-hidden="true">{STAGES.map(item => <i key={item.no} className={item.no < no ? 'is-done' : item.no === no ? 'is-now' : ''} />)}</span>
    <span className="dr-tracker-label"><b>{no}</b>{stage.title}</span>
  </span>
}

/** The 48-hour clock of a task: it opens 48 hours before it is due. */
export function SlaMeter({ dueAt }: { dueAt: string }) {
  const state = slaState(new Date(new Date(dueAt).getTime() - 48 * 3_600_000))
  return <span className={`dr-sla is-${state.urgency}`} title="48-hour limit for this task">
    <span className="dr-sla-bar" aria-hidden="true"><i style={{ width: `${Math.round(state.fill * 100)}%` }} /></span>
    <span className="dr-sla-label">{state.label}</span>
  </span>
}
