import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { cn } from '@/shared/lib/cn'

export interface StatTileData {
  key: string
  /** One short plain-English label ("Candidates to work today"). */
  label: string
  /** The single number (or short value) the tile is about. */
  value: ReactNode
  /** Small unit or context printed beside the number ("of 27"). */
  unit?: string
  /** One muted line under the number. */
  detail?: ReactNode
  icon: LucideIcon
  /** Only the ink of the icon: green and amber and red carry meaning, blue is neutral. */
  tone?: 'blue' | 'green' | 'amber' | 'red'
  /** A done / total pair draws a slim progress bar under the number. */
  progress?: { done: number; total: number }
  /** Makes the tile a button (a filter or a link). */
  onSelect?: () => void
  active?: boolean
  /** Accessible name of the action ("Show only recruiters with nothing done"). */
  actionLabel?: string
}

/** The compact summary strip at the top of the admin Tasks tabs: four short tiles, one number each. */
export function StatStrip({ tiles, label }: { tiles: StatTileData[]; label: string }) {
  return (
    <ul className={cn('dwb-stats', `has-${tiles.length}`)} aria-label={label}>
      {tiles.map((tile) => <li key={tile.key}><StatTile tile={tile} /></li>)}
    </ul>
  )
}

function StatTile({ tile }: { tile: StatTileData }) {
  const { icon: Icon, tone = 'blue', progress, onSelect, active } = tile
  const percent = progress && progress.total > 0 ? Math.min(100, Math.round((progress.done / progress.total) * 100)) : 0
  const body = (
    <>
      <span className={cn('dwb-stat-icon', `tone-${tone}`)} aria-hidden="true"><Icon size={18} strokeWidth={1.8} /></span>
      <span className="dwb-stat-text">
        <span className="dwb-stat-label">{tile.label}</span>
        <span className="dwb-stat-figure"><strong className="tnum">{tile.value}</strong>{tile.unit && <span className="tnum">{tile.unit}</span>}</span>
        {progress
          ? <span className="dwb-meter" role="img" aria-label={`${percent} percent done`}><i style={{ width: `${percent}%` }} /></span>
          : tile.detail && <span className="dwb-stat-detail">{tile.detail}</span>}
        {progress && tile.detail && <span className="dwb-stat-detail">{tile.detail}</span>}
      </span>
    </>
  )
  if (!onSelect) return <div className="dwb-stat">{body}</div>
  return (
    <button type="button" className={cn('dwb-stat is-action', active && 'is-active')} aria-pressed={active === undefined ? undefined : !!active}
      aria-label={tile.actionLabel ? `${tile.label}: ${tile.value}. ${tile.actionLabel}` : undefined} onClick={onSelect}>
      {body}
    </button>
  )
}
