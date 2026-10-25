import type { ReactNode } from 'react'
import { cn } from '@/shared/lib/cn'

/**
 * EmptyState: an honest "nothing here" block drawn as a short route (three nodes), never fake data.
 *
 * Props
 *  - title        string   what is missing, in plain English
 *  - description? string   how to fill it (one sentence)
 *  - action?      node     a Button
 *  - icon?        node     optional lucide icon shown in the middle node (size 20)
 *  - compact?     boolean  smaller padding for use inside a panel or a list column
 */
export function EmptyState({ title, description, action, icon, compact, className }: { title: string; description?: string; action?: ReactNode; icon?: ReactNode; compact?: boolean; className?: string }) {
  return (
    <div className={cn('v2-empty ui-empty-state', compact && 'is-compact', className)}>
      <div className="v2-empty-art" aria-hidden="true">
        <i /><b />{icon ? <span className="v2-empty-icon">{icon}</span> : <i className="v2-empty-mid" />}<b /><i />
      </div>
      <p className="v2-empty-title">{title}</p>
      {description && <p className="v2-empty-desc">{description}</p>}
      {action && <div className="v2-empty-action">{action}</div>}
    </div>
  )
}
