import type { ReactNode } from 'react'
import { cn } from '@/shared/lib/cn'

/**
 * Tag: a node chip. A small dot-node plus text, outlined in the status colour (no filled pill).
 *
 * Props
 *  - tone?     'neutral' | 'primary' | 'success' | 'warning' | 'danger'   default 'neutral'
 *  - dot?      boolean   show the node dot (default true for every tone except neutral)
 *  - size?     'sm' | 'md'   default 'md'
 *  - children  the label (plain English, sentence case)
 */
export type TagTone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger'

export function Tag({ tone = 'neutral', dot, size = 'md', children, className }: { tone?: TagTone; dot?: boolean; size?: 'sm' | 'md'; children: ReactNode; className?: string }) {
  const showDot = dot ?? tone !== 'neutral'
  return (
    <span className={cn('v2-tag', size === 'sm' && 'is-sm', className)} data-tone={tone}>
      {showDot && <i className="v2-tag-node" aria-hidden="true" />}
      <span className="v2-tag-text">{children}</span>
    </span>
  )
}
