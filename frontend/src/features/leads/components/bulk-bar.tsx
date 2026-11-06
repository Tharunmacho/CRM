import { Download, Trash2, UserCheck, UserRoundX, ListChecks, X } from 'lucide-react'
import { Button } from '@/shared/ui/primitives'
import { allMatchingBlock, canOfferAllMatching, selectionCount, selectionLine, type BulkAction, type Selection } from '../lead-bulk-model'
import '@/styles/lead-bulk.css'

/**
 * The bar that appears above the table while leads are ticked: how many, "select all N matching" (across pages), and the
 * actions. It sticks to the top of the card while the table scrolls. Actions are chosen here; the dialog that confirms the
 * exact count and does the work is BulkDialog.
 */
export function BulkBar({ selection, matchingTotal, canDelete, busy, exporting, onSelectAll, onClear, onAction }: {
  selection: Selection
  /** Leads the current search and filters match, across every page. */
  matchingTotal: number
  canDelete: boolean
  busy: boolean
  exporting: boolean
  onSelectAll: () => void
  onClear: () => void
  onAction: (action: BulkAction) => void
}) {
  const n = selectionCount(selection, matchingTotal)
  if (n === 0) return null
  const offer = canOfferAllMatching(selection, matchingTotal)
  const tooMany = allMatchingBlock(matchingTotal)
  const overCap = selection.allMatching && tooMany
  return (
    <div className="ld-bulk" role="region" aria-label="Actions for the selected leads">
      <div className="ld-bulk-who">
        <strong className="ld-bulk-count tnum" role="status">{selectionLine(selection, matchingTotal)}</strong>
        {offer && (tooMany
          ? <span className="ld-bulk-note" title={tooMany}>{matchingTotal.toLocaleString()} match: too many to select at once</span>
          : <button type="button" className="ld-bulk-link" onClick={onSelectAll}><ListChecks size={14} aria-hidden="true" />Select all {matchingTotal.toLocaleString()} matching leads</button>)}
        {overCap && <span className="ld-bulk-note is-warn" role="alert">{tooMany}</span>}
      </div>
      <div className="ld-bulk-acts">
        <Button size="sm" variant="secondary" icon={<UserCheck />} disabled={busy || Boolean(overCap)} onClick={() => onAction('assign')}>Assign to…</Button>
        <Button size="sm" variant="secondary" disabled={busy || Boolean(overCap)} onClick={() => onAction('status')}>Change status…</Button>
        <Button size="sm" variant="secondary" icon={<UserRoundX />} disabled={busy || Boolean(overCap)} onClick={() => onAction('not_interested')}>Mark not interested</Button>
        <Button size="sm" variant="secondary" icon={<Download />} disabled={busy || exporting || Boolean(overCap)} onClick={() => onAction('export')}>{exporting ? 'Preparing…' : 'Export selected'}</Button>
        {canDelete && <Button size="sm" variant="dangerGhost" icon={<Trash2 />} disabled={busy || Boolean(overCap)} onClick={() => onAction('delete')}>Delete…</Button>}
        <Button size="sm" variant="ghost" icon={<X />} disabled={busy} onClick={onClear}>Clear selection</Button>
      </div>
    </div>
  )
}
