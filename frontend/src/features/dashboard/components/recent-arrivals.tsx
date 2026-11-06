import type { CSSProperties } from 'react'
import { ArrowRight, type LucideIcon } from 'lucide-react'
import { ago } from '@/shared/lib/format'
import { Avatar, Pill, SOURCE_META } from '@/features/dashboard/components/identity'
import { colourOf, type RecentRow } from '@/features/dashboard/components/intake-model'

export interface AttentionItem { key: string; label: string; hint: string; count: number; icon: LucideIcon; tone: 'warn' | 'bad' | 'info'; onClick: () => void }

type Tone = 'info' | 'warn' | 'good' | 'bad' | 'muted' | 'accent'

/** The newest candidates, newest first: who, where from, when, and what state they are in. */
export function RecentFeed({ rows, statusOf, onOpen }: { rows: RecentRow[]; statusOf: (status: string) => { label: string; tone: Tone }; onOpen: (id: string) => void }) {
  return <ul className="dc-feed" aria-label="Recent arrivals, newest first">
    {rows.map(row => {
      const meta = SOURCE_META[row.source] ?? SOURCE_META.other
      const status = statusOf(row.status)
      return <li key={row.id}>
        <button type="button" className="dc-feed-row" onClick={() => onOpen(row.id)} aria-label={`Open ${row.name}, added ${ago(row.addedAt)} by ${meta.label}`}>
          <Avatar name={row.name} />
          <span className="dc-feed-main">
            <strong>{row.name}</strong>
            <small><span className="dc-feed-src" style={{ '--tone': colourOf(row.source in SOURCE_META ? row.source : 'other') } as CSSProperties}><meta.icon size={12} aria-hidden="true" />{meta.label}</span><span className="dc-feed-role">{row.role}</span></small>
          </span>
          <span className="dc-feed-end"><time dateTime={row.addedAt}>{ago(row.addedAt)}</time><Pill tone={status.tone}>{status.label}</Pill></span>
          <ArrowRight className="dc-feed-go" size={15} aria-hidden="true" />
        </button>
      </li>
    })}
  </ul>
}

/** What needs a person right now. Only the items that have something waiting are shown; each opens the exact list. */
export function AttentionList({ items }: { items: AttentionItem[] }) {
  return <ul className="dc-attn">
    {items.map(item => <li key={item.key}>
      <button type="button" className={`dc-attn-item is-${item.tone}`} onClick={item.onClick} aria-label={`${item.count} ${item.label}. ${item.hint}`}>
        <span className="dc-attn-ico" aria-hidden="true"><item.icon size={18} strokeWidth={1.9} /></span>
        <span className="dc-attn-text"><b>{item.count.toLocaleString()}</b><em>{item.label}</em></span>
        <ArrowRight className="dc-attn-go" size={15} aria-hidden="true" />
      </button>
    </li>)}
  </ul>
}
