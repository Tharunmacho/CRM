import type { CSSProperties, KeyboardEvent, ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { initials } from '@/shared/lib/format'
import { cn } from '@/shared/lib/cn'

type KpiTone = 'blue' | 'green' | 'amber' | 'violet' | 'red'

interface SummaryItem {
  label: string
  value: ReactNode
  detail: string
  icon: LucideIcon
  /** Only changes the ink of the icon (green, amber, red carry meaning); blue and violet stay neutral. */
  tone?: KpiTone
  /** Makes the card a toggle that applies (or clears) a list filter. */
  onSelect?: () => void
  active?: boolean
}

/**
 * The reference product's KPI row: separate bordered cards, label top-left, a 34px bordered icon
 * square top-right, the big figure and a muted caption. A selected card gets a primary border.
 * Styles: styles/workspace.css (.workspace-summary / .summary-card).
 */
export function SummaryCards({ items }: { items: SummaryItem[] }) {
  return (
    <div className="workspace-summary" style={{ '--summary-columns': items.length } as CSSProperties}>
      {items.map(({ label, value, detail, icon: Icon, tone = 'blue', onSelect, active }) => (
        <section
          className={cn('summary-card', onSelect && 'is-action', active && 'is-active')}
          key={label}
          aria-label={label}
          {...(onSelect
            ? {
                role: 'button',
                tabIndex: 0,
                'aria-pressed': !!active,
                onClick: onSelect,
                onKeyDown: (event: KeyboardEvent<HTMLElement>) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    onSelect()
                  }
                },
              }
            : {})}
        >
          <div className="summary-card-top">
            <span>{label}</span>
            <span className={`summary-icon tone-${tone}`}>
              <Icon size={17} strokeWidth={1.8} />
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
  // A deterministic soft hue per name, so the same person is always the same colour.
  const hue = name.split('').reduce((total, character) => total + character.charCodeAt(0), 0) % 6
  return (
    <div className="record-identity">
      <span className={cn('record-avatar', `avatar-hue-${hue}`)} aria-hidden="true">
        {Icon ? <Icon size={17} strokeWidth={1.8} /> : initials(name)}
      </span>
      <div className="min-w-0">
        <p className="record-name">{name}</p>
        {detail && <p className="record-detail">{detail}</p>}
      </div>
    </div>
  )
}

interface KpiItem {
  label: string
  value: ReactNode
  detail: string
  icon: LucideIcon
  tone?: KpiTone
  /** Turns the card into a toggle that applies (or clears) a list filter. */
  onSelect?: () => void
  active?: boolean
}

/** Same card row as SummaryCards (kept as its own export for the screens that import it). */
export function KpiStrip({ items }: { items: KpiItem[] }) {
  return (
    <div className="workspace-summary" style={{ '--summary-columns': items.length } as CSSProperties}>
      {items.map(({ label, value, detail, icon: Icon, tone = 'blue', onSelect, active }) => {
        const body = (
          <>
            <span className="summary-card-top">
              <span>{label}</span>
              <span className={`summary-icon tone-${tone}`}>
                <Icon size={17} strokeWidth={1.8} />
              </span>
            </span>
            <strong className="summary-value tnum">{value}</strong>
            <span className="summary-caption">{detail}</span>
          </>
        )
        return onSelect ? (
          <button
            key={label}
            type="button"
            className={cn('summary-card is-action', active && 'is-active')}
            aria-pressed={!!active}
            onClick={onSelect}
          >
            {body}
          </button>
        ) : (
          <section key={label} className="summary-card" aria-label={label}>
            {body}
          </section>
        )
      })}
    </div>
  )
}
