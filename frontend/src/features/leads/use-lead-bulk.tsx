import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { User } from '@/shared/model/types'
import { gateFromRefusal, leadApi } from './lead-api'
import { bulkErrorMessage, downloadSelectedLeads, leadBulkApi } from './lead-bulk-api'
import {
  EMPTY_SELECTION, bulkBody, changeSelection, filterFromQuery, resultToast, resultVerb, selectAllMatching, selectionCount, statusToast, undoAlive, undoBody, undoEntry, undoneToast, visibleSelected,
  type BulkAction, type BulkOptions, type Selection, type UndoEntry,
} from './lead-bulk-model'
import { leadStatus, type Lead } from './lead-model'
import type { LeadGate } from './lead-gate'
import { BulkBar } from './components/bulk-bar'
import { BulkDialog, DeleteLeadDialog, StatusDialog } from './components/bulk-dialogs'
import { StatusUndoToast } from './components/status-undo-toast'

interface Params {
  /** The list's query (buildLeadQuery: filters and sort, no paging): the same filter the bulk call uses for "all matching". */
  query: string
  sort: { key: string; dir: 'asc' | 'desc' }
  pageSize: number
  rows: Lead[]
  /** Leads the filters match across every page. */
  total: number
  users: User[]
  supervisor: boolean
  isAdmin: boolean
  refresh: () => void
  flash: (text: string) => void
  onGateRefusal: (gate: LeadGate) => void
  /** A lead was deleted: close anything showing it. */
  onDeleted: (lead: Lead) => void
}

type Written = Exclude<BulkAction, 'export'>

/**
 * Everything the Lead sheet does with selected leads and with one lead's status: the selection (kept across pages; "all
 * matching" is filter based), the bulk bar and its confirm dialog, status change with an undo toast, and delete.
 * Pure rules live in lead-bulk-model.ts; requests in lead-bulk-api.ts.
 */
export function useLeadBulk(params: Params) {
  const { query, sort, pageSize, rows, total, users, supervisor, isAdmin, refresh, flash, onGateRefusal, onDeleted } = params
  const [selection, setSelection] = useState<Selection>(EMPTY_SELECTION)
  const [action, setAction] = useState<Written | null>(null)
  const [exporting, setExporting] = useState(false)
  const [statusLead, setStatusLead] = useState<Lead | null>(null)
  const [deleteLead, setDeleteLead] = useState<Lead | null>(null)
  const [undo, setUndo] = useState<{ entry: UndoEntry; text: string } | null>(null)
  const [undoBusy, setUndoBusy] = useState(false)

  // A different search, filter, sort or page size is a different list: the selection does not carry over.
  const resetKey = `${query}|${pageSize}`
  const lastKey = useRef(resetKey)
  useEffect(() => { if (lastKey.current !== resetKey) { lastKey.current = resetKey; setSelection(EMPTY_SELECTION); setAction(null) } }, [resetKey])

  const filter = useMemo(() => filterFromQuery(query), [query])
  const pageIds = useMemo(() => rows.map((row) => row.id), [rows])
  const count = selectionCount(selection, total)
  const clear = useCallback(() => setSelection(EMPTY_SELECTION), [])

  /** Run a single-lead status change (PATCH). Throws the server's refusal after the daily-work gate is told about it. */
  const applyStatus = useCallback(async (lead: Lead, to: string, announce = true) => {
    const entry = undoEntry(lead, to)
    if (!entry) return
    try { await leadApi.update(lead.id, { status: to }) } catch (error) {
      const refused = gateFromRefusal(error)
      if (refused) onGateRefusal(refused)
      throw error
    }
    if (announce) setUndo({ entry, text: statusToast(to, (value) => leadStatus(value).label) })
    refresh()
  }, [onGateRefusal, refresh])

  /** The drawer saved a new status itself: offer the same undo. */
  const offerUndo = useCallback((lead: Pick<Lead, 'id' | 'name' | 'status'>, from: string, to: string) => {
    const entry = undoEntry({ ...lead, status: from }, to)
    if (entry) setUndo({ entry, text: statusToast(to, (value) => leadStatus(value).label) })
  }, [])

  const quickStatus = useCallback(async (lead: Lead, to: string) => {
    try { await applyStatus(lead, to) } catch (error) { flash(bulkErrorMessage(error, 'We could not update the lead.')) }
  }, [applyStatus, flash])

  const runUndo = async () => {
    if (!undo || !undoAlive(undo.entry)) { setUndo(null); return }
    setUndoBusy(true)
    try {
      await leadApi.update(undo.entry.leadId, undoBody(undo.entry))
      const text = undoneToast(undo.entry, (value) => leadStatus(value).label)
      setUndo(null); flash(text); refresh()
    } catch (error) {
      setUndo(null); flash(bulkErrorMessage(error, 'We could not undo that.'))
    } finally { setUndoBusy(false) }
  }

  const confirm = async (options: BulkOptions) => {
    if (!action) return
    const result = await leadBulkApi.run(bulkBody(selection, filter, total, action, options))
    setAction(null); setSelection(EMPTY_SELECTION)
    flash(resultToast(result, resultVerb(action)))
    refresh()
  }

  const exportSelected = async () => {
    setExporting(true)
    try {
      const made = await downloadSelectedLeads(bulkBody(selection, filter, total, 'export', { sort: sort.key, direction: sort.dir }))
      flash(`${made.toLocaleString()} ${made === 1 ? 'lead' : 'leads'} exported to Excel.`)
    } catch (error) { flash(bulkErrorMessage(error, 'We could not make the Excel file.')) } finally { setExporting(false) }
  }

  const removeOne = async (lead: Lead) => {
    await leadBulkApi.remove(lead.id)
    setDeleteLead(null); onDeleted(lead)
    setSelection((current) => ({ ...current, ids: current.ids.filter((id) => id !== lead.id) }))
    flash(`${lead.name || 'The lead'} was deleted.`)
    refresh()
  }

  const bar: ReactNode = supervisor
    ? <BulkBar selection={selection} matchingTotal={total} canDelete={isAdmin} busy={Boolean(action)} exporting={exporting}
        onSelectAll={() => setSelection(selectAllMatching(selection))} onClear={clear}
        onAction={(next) => { if (next === 'export') void exportSelected(); else setAction(next) }} />
    : null

  const overlays: ReactNode = (
    <>
      <BulkDialog action={action} count={count} allMatching={selection.allMatching} users={users} onClose={() => setAction(null)} onConfirm={confirm} />
      <StatusDialog lead={statusLead} onClose={() => setStatusLead(null)} onConfirm={async (lead, to) => { await applyStatus(lead, to); setStatusLead(null) }} />
      <DeleteLeadDialog lead={deleteLead} onClose={() => setDeleteLead(null)} onConfirm={removeOne} />
      {undo && <StatusUndoToast text={undo.text} busy={undoBusy} onUndo={() => void runUndo()} onClose={() => setUndo(null)} />}
    </>
  )

  return {
    /** The ids the table / cards show ticked. */
    selectedIds: visibleSelected(selection, pageIds),
    onSelectedChange: (ids: string[]) => setSelection((current) => changeSelection(current, ids)),
    clear, bar, overlays, quickStatus, offerUndo,
    openStatus: setStatusLead, openDelete: setDeleteLead,
  }
}
