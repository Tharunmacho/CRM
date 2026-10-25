import type { CSSProperties, ReactNode } from 'react'
import { cn } from '@/shared/lib/cn'

/**
 * Skeletons: flat placeholder shapes in the same language as the real screen (a very subtle pulse, no shimmer gradient).
 * Wrap a group in <SkeletonGroup label="Loading clients"> so assistive tech hears one "loading" message.
 *
 *  - Skeleton({ width?, height?, radius?, className })     one block
 *  - SkeletonText({ lines?, lastShort? })                   paragraph lines
 *  - SkeletonAvatar({ size? })                              rounded-square avatar
 *  - SkeletonTile()                                         stat tile
 *  - SkeletonRow({ avatar?, cells? })                       list row (identity + cells)
 *  - SkeletonTable({ rows?, cols? })                        header rule + hairline rows
 *  - SkeletonRoute({ size? })                               11 hollow nodes on a line
 *  - SkeletonPanel({ rows?, children? })                    panel with a header stub
 *  - SkeletonGroup({ label, children })                     role="status" wrapper
 */
export function Skeleton({ width, height = 14, radius, className }: { width?: number | string; height?: number | string; radius?: number | string; className?: string }) {
  return <span className={cn('v2-sk', className)} aria-hidden="true" style={{ width, height, borderRadius: radius } as CSSProperties} />
}

export function SkeletonGroup({ label = 'Loading', children, className }: { label?: string; children: ReactNode; className?: string }) {
  return <div role="status" aria-busy="true" aria-label={label} className={cn('v2-sk-group', className)}><span className="sr-only">{label}…</span>{children}</div>
}

export function SkeletonText({ lines = 3, lastShort = true }: { lines?: number; lastShort?: boolean }) {
  return <span className="v2-sk-text" aria-hidden="true">{Array.from({ length: lines }, (_, i) => <Skeleton key={i} height={12} width={lastShort && i === lines - 1 && lines > 1 ? '60%' : '100%'} />)}</span>
}

export function SkeletonAvatar({ size = 40 }: { size?: number }) {
  return <Skeleton width={size} height={size} radius={Math.round(size * 0.28)} className="v2-sk-avatar" />
}

export function SkeletonTile() {
  return <div className="v2-tile v2-sk-tile" aria-hidden="true"><Skeleton width="45%" height={12} /><Skeleton width="38%" height={30} radius={8} /><Skeleton width="60%" height={11} /></div>
}

export function SkeletonRow({ avatar = true, cells = 3 }: { avatar?: boolean; cells?: number }) {
  return (
    <div className="v2-sk-row" aria-hidden="true">
      {avatar && <SkeletonAvatar size={36} />}
      <span className="v2-sk-row-id"><Skeleton width="55%" height={13} /><Skeleton width="35%" height={11} /></span>
      {Array.from({ length: cells }, (_, i) => <Skeleton key={i} width={72} height={12} className={i > 0 ? 'v2-sk-hide-sm' : undefined} />)}
    </div>
  )
}

export function SkeletonTable({ rows = 6, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="v2-sk-table" aria-hidden="true">
      <div className="v2-sk-thead">{Array.from({ length: cols }, (_, i) => <Skeleton key={i} width={i === 0 ? 96 : 64} height={11} />)}</div>
      {Array.from({ length: rows }, (_, i) => <SkeletonRow key={i} cells={Math.max(0, cols - 1)} />)}
    </div>
  )
}

export function SkeletonRoute({ size = 'md' }: { size?: 'sm' | 'md' }) {
  return <span className={cn('v2-sk-route', size === 'sm' && 'is-sm')} aria-hidden="true">{Array.from({ length: 11 }, (_, i) => <i key={i} />)}</span>
}

export function SkeletonPanel({ children, rows = 3 }: { children?: ReactNode; rows?: number }) {
  return (
    <div className="v2-panel v2-sk-panel" aria-hidden="true">
      <div className="v2-panel-head"><div className="v2-panel-heading"><span className="v2-stub" /><Skeleton width={140} height={16} /></div></div>
      <div className="v2-panel-body">{children ?? <SkeletonText lines={rows} />}</div>
    </div>
  )
}
