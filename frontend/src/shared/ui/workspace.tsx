import type { CSSProperties, KeyboardEvent, ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { initials } from '@/shared/lib/format'
import { cn } from '@/shared/lib/cn'

interface SummaryItem {
  label: string
  value: ReactNode
  detail: string
  icon: LucideIcon
  tone?: 'blue' | 'green' | 'amber' | 'violet'
  /** Makes the card a toggle that applies (or clears) a list filter. */
  onSelect?: () => void
  active?: boolean
}

export function SummaryCards({ items }: { items: SummaryItem[] }) {
  return (
    <div
      className="workspace-summary"
      style={{ '--summary-columns': items.length } as CSSProperties}
    >
      {items.map(({ label, value, detail, icon: Icon, tone = 'blue', onSelect, active }) => (
        <section
          className={cn('summary-card', onSelect && 'cursor-pointer transition-shadow hover:shadow-soft focus-visible:outline focus-visible:outline-2 focus-visible:outline-navy-600', active && 'ring-2 ring-navy-600')}
          key={label}
          aria-label={label}
          {...(onSelect ? {
            role: 'button', tabIndex: 0, 'aria-pressed': !!active, onClick: onSelect,
            onKeyDown: (event: KeyboardEvent<HTMLElement>) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect() } },
          } : {})}
        >
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
