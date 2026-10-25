import { Footprints, Link2, Mail, MessageCircle, UserPlus, type LucideIcon } from 'lucide-react'
import { CountryFlag } from '@/shared/ui/country-flag'
import { SOURCE_COLOURS } from './report-config'

/** Care India's own touches in the reports: country flags, source icons and stage chips. */

export function Flag({ country }: { country?: string | null }) {
  return <CountryFlag country={country} className="rv-flag" globeClassName="rv-flag-globe" title={country ?? undefined} />
}

export const SOURCE_META: Record<string, { label: string; icon: LucideIcon }> = {
  email: { label: 'Email', icon: Mail }, whatsapp: { label: 'WhatsApp', icon: MessageCircle }, walk_in: { label: 'Walk-in', icon: Footprints },
  referral: { label: 'Referral', icon: UserPlus }, other: { label: 'Other', icon: Link2 },
}

export function SourceTag({ source, label }: { source: string; label?: string }) {
  const meta = SOURCE_META[source] ?? SOURCE_META.other
  return <span className="rv-source" style={{ ['--tone' as string]: SOURCE_COLOURS[source] ?? SOURCE_COLOURS.other }}>
    <i aria-hidden="true"><meta.icon size={14} strokeWidth={1.9} /></i>{label ?? meta.label}
  </span>
}

/** A numbered stage marker with its name, e.g. (4) Interview. */
export function StageChip({ stage, label }: { stage: number; label: string }) {
  return <span className="rv-stage" title={`Stage ${stage}: ${label}`}><b className="tnum">{stage}</b><span>{label}</span></span>
}

/** The deployed / openings progress bar used on orders. */
export function FillBar({ value, total, tone = 'primary' }: { value: number; total: number; tone?: 'primary' | 'success' }) {
  const share = total > 0 ? Math.min(100, Math.round((value / total) * 100)) : 0
  return <span className={`rv-fill is-${tone}`} title={`${value} of ${total} (${share}%)`}>
    <span className="rv-fill-track" aria-hidden="true"><i style={{ width: `${share}%` }} /></span>
  </span>
}
