import type { CSSProperties, ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { initials } from '@/shared/lib/format'
import { cn } from '@/shared/lib/cn'

interface SummaryItem {
  label: string
  value: ReactNode
  detail: string
  icon: LucideIcon
  tone?: 'blue' | 'green' | 'amber' | 'violet'
}

export function SummaryCards({ items }: { items: SummaryItem[] }) {
  return (
    <div
      className="workspace-summary"
      style={{ '--summary-columns': items.length } as CSSProperties}
    >
      {items.map(({ label, value, detail, icon: Icon, tone = 'blue' }) => (
        <section className="summary-card" key={label} aria-label={label}>
          <div className="summary-card-top">
            <span>{label}</span>
            <span className={`summary-icon tone-${tone}`}>
              <Icon size={18} strokeWidth={1.7} />
            </span>
          </div>
          <strong className="summary-value tnum">{value}</strong>
          <p>{detail}</p>
        </section>
      ))}
    </div>
  )
}

export function RecordIdentity({
  name,
  detail,
  icon: Icon,
}: {
  name: string
  detail?: ReactNode
  icon?: LucideIcon
}) {
  const tone = name.split('').reduce((total, character) => total + character.charCodeAt(0), 0) % 4
  return (
    <div className="record-identity">
      <span className={cn('record-avatar', `avatar-tone-${tone}`)} aria-hidden="true">
        {Icon ? <Icon size={18} strokeWidth={1.7} /> : initials(name)}
      </span>
      <div className="min-w-0">
        <p className="record-name">{name}</p>
        {detail && <p className="record-detail">{detail}</p>}
      </div>
    </div>
  )
}
