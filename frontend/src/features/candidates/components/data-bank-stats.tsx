import { FileSearch, UserCheck, Users, type LucideIcon } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import type { StatCard, StatKey } from '@/features/candidates/data-bank-stats'
import '@/styles/data-bank-top.css'

const ICON: Record<StatKey, LucideIcon> = { resumes: Users, unassigned: UserCheck, needs_review: FileSearch }

/** Three compact number cards, each a button that applies its filter: icon chip, big number, tiny label, nothing else. */
export function DataBankStats({ cards, onPress }: { cards: StatCard[]; onPress: (card: StatCard) => void }) {
  return (
    <div className="dbs" data-count={cards.length}>
      {cards.map(card => {
        const Icon = ICON[card.key]
        return (
          <button key={card.key} type="button" className={cn('dbs-card', `is-${card.tone}`, card.active && 'is-on', card.muted && 'is-muted')} aria-pressed={card.active} onClick={() => onPress(card)}>
            <span className="dbs-chip" aria-hidden="true"><Icon size={18} strokeWidth={1.9} /></span>
            <span className="dbs-text">
              <strong className="dbs-value tnum">{card.value}</strong>
              <span className="dbs-label">{card.label}</span>
            </span>
          </button>
        )
      })}
    </div>
  )
}
