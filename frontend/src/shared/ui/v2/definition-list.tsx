import type { CSSProperties, ReactNode } from 'react'
import { cn } from '@/shared/lib/cn'

/**
 * DefinitionList: aligned label / value pairs.
 *
 * Props
 *  - items?    { label, value, wide? }[]   or pass <DefinitionItem label>…</DefinitionItem> children
 *  - columns?  1 | 2 | 3   columns on wide screens (default 2); collapses to 1 on phones
 *  - layout?   'stacked' (label above value, default) | 'inline' (label left, value right, hairline between rows)
 *  Empty values show a muted dash. Long values wrap (never clipped).
 */
export interface DefinitionEntry { label: string; value: ReactNode; wide?: boolean }

export function DefinitionItem({ label, children, wide }: { label: string; children?: ReactNode; wide?: boolean }) {
  const empty = children == null || children === '' || children === false
  return (
    <div className={cn('v2-def', wide && 'is-wide')}>
      <dt>{label}</dt>
      <dd className={empty ? 'is-empty' : undefined}>{empty ? '—' : children}</dd>
    </div>
  )
}

export function DefinitionList({ items, columns = 2, layout = 'stacked', children, className }: { items?: DefinitionEntry[]; columns?: 1 | 2 | 3; layout?: 'stacked' | 'inline'; children?: ReactNode; className?: string }) {
  return (
    <dl className={cn('v2-deflist', `is-${layout}`, className)} style={{ '--v2-def-cols': columns } as CSSProperties}>
      {items?.map((item) => <DefinitionItem key={item.label} label={item.label} wide={item.wide}>{item.value}</DefinitionItem>)}
      {children}
    </dl>
  )
}
