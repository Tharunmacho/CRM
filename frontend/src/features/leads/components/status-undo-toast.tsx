import { useEffect } from 'react'
import { UNDO_MS } from '../lead-bulk-model'
import '@/styles/lead-bulk.css'

/** A toast with an Undo button for a status change ("Marked not interested. Undo"). It closes itself after UNDO_MS, which is also how long Undo works. */
export function StatusUndoToast({ text, busy, onUndo, onClose }: { text: string; busy: boolean; onUndo: () => void; onClose: () => void }) {
  useEffect(() => {
    const timer = window.setTimeout(onClose, UNDO_MS)
    return () => window.clearTimeout(timer)
  }, [text, onClose])
  return (
    <div className="ld-sundo" role="status">
      <span>{text}</span>
      <button type="button" className="ld-sundo-btn" disabled={busy} onClick={onUndo}>{busy ? 'Undoing…' : 'Undo'}</button>
    </div>
  )
}
