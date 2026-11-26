import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, CalendarPlus, ClipboardList, Clock3, Copy, FileSpreadsheet, History, Loader2, Lock, Pencil, Plus, RefreshCw, Settings2, Trash2, X } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { isAdminLevel } from '@/shared/model/role-labels'
import { COUNTRY_NAMES } from '@/shared/lib/country-flag'
import { useAgencyToday } from '@/features/daily-report/use-agency-today'
import { cn } from '@/shared/lib/cn'
import { Button, SearchInput, Select } from '@/shared/ui/primitives'
import { DatePicker } from '@/shared/ui/date-picker'
import { EmptyState, PageHeader } from '@/shared/ui/display'
import { SummaryCards } from '@/shared/ui/workspace'
import { RowMenu, type RowMenuItem } from '@/shared/ui/table'
import {
  CUSTOM_WIDTH, FIXED_WIDTHS, NO_FILTERS, STATUS_TABS, buildRows, columnWidths, positionsSummary, canDeleteBy, daysBefore, duplicateBody, filterCount, flipStatus, isoToText, localSummary, ownerChoices, shapeDateText, summaryCounts, whoWhen,
  type ClosedReason, type ListFilters, type RequirementField, type RequirementItem, type RequirementPatch, type RowFilter, type StatusFilter,
} from '@/features/job-requirements/job-requirements-model'
import { downloadRequirementForm, downloadRequirements } from '@/features/job-requirements/job-requirements-api'
import { useJobRequirements } from '@/features/job-requirements/use-job-requirements'
import { AdCell, ColumnHead, CustomCell, DetailsButton, InlineCell, SaveMark, StatusToggle, dateRead, identity, textRead } from './job-requirement-cells'
import { CloseDialog, ConfirmDialog, FieldDialog, HistoryDrawer } from './job-requirement-dialogs'
import { JobRequirementForm } from './job-requirement-form'
import { FieldManagerDialog } from './job-requirement-fields'
import { OwnersCell } from './job-requirement-owners'
import '@/styles/job-requirements.css'

const FIXED = 9

type Dialogs =
  | { kind: 'details'; id: string }
  | { kind: 'close'; id: string }
  | { kind: 'history'; id: string }
  | { kind: 'field'; field?: RequirementField }
  | { kind: 'delete-row'; id: string }
  | { kind: 'delete-field'; field: RequirementField }
  | null

const longest = (texts: string[]) => texts.reduce((max, text) => Math.max(max, text.trim().length), 0)

const VIEW_NAMES: Partial<Record<RowFilter, string>> = { dueSoon: 'Due in 7 days', overdue: 'Overdue' }

/**
 * Job requirements: a sheet the recruiters fill in by hand, like a spreadsheet, with the management view on top (summary cards, filters, export).
 * Every cell is typed in place and saved when it is left; "+ New requirement" saves a blank row at once and puts the cursor in its Client name.
 * Columns the team adds sit after Status. Closing asks why; History shows who changed what.
 */
export default function JobRequirementsPage() {
  const store = useStore()
  const notify = store.notify
  const admin = canDeleteBy(isAdminLevel(store.me.role))
  const [typed, setTyped] = useState('')
  const [filters, setFilters] = useState<ListFilters>(NO_FILTERS)
  const [view, setView] = useState<RowFilter>('all')
  const [dialog, setDialog] = useState<Dialogs>(null)
  const [creating, setCreating] = useState(false)
  const [boxWidth, setBoxWidth] = useState(0)
  const watch = useRef<ResizeObserver | null>(null)
  /** The width of the table box, kept current: the table is made exactly that wide (no scroll bar) when the columns fit. */
  const boxRef = useCallback((node: HTMLDivElement | null) => {
    watch.current?.disconnect()
    if (!node) return
    setBoxWidth(Math.round(node.clientWidth))
    if (typeof ResizeObserver === 'undefined') return
    watch.current = new ResizeObserver(() => setBoxWidth(Math.round(node.clientWidth)))
    watch.current.observe(node)
  }, [])
  const [exporting, setExporting] = useState(false)
  const [focusId, setFocusId] = useState<string | null>(null)
  const clientBoxes = useRef<Record<string, HTMLElement | null>>({})
  const sent = useMemo<ListFilters>(() => ({ ...filters, due: view === 'dueSoon' ? 'soon' : view === 'overdue' ? 'overdue' : '' }), [filters, view])
  const people = useMemo(() => ownerChoices(store.users), [store.users])
  const data = useJobRequirements(sent, people)
  const [managing, setManaging] = useState(false)
  const { items, fields, loading, error } = data
  const today = useAgencyToday(store.settings.timezone || store.agencyTimezone || 'Asia/Kolkata')   // due-soon / overdue follow the new day at midnight

  useEffect(() => {
    const wanted = typed.replace(/\s+/g, ' ').trim()
    if (wanted === filters.q) return undefined
    const timer = window.setTimeout(() => setFilters((current) => ({ ...current, q: wanted })), 300)
    return () => window.clearTimeout(timer)
  }, [typed, filters.q])

  const setFilter = (key: keyof ListFilters, value: string) => setFilters((current) => ({ ...current, [key]: value }))
  const clearFilters = () => { setTyped(''); setFilters(NO_FILTERS); setView('all') }

  const rows = useMemo(() => buildRows(items, view, today), [items, view, today])
  const summary = data.summary ?? localSummary(items, today)
  const counts = summaryCounts(summary)

  // The Client and Country pickers keep every value seen, so choosing one does not make the others disappear.
  const seen = useRef({ clients: new Set<string>(), countries: new Set<string>() })
  for (const item of items) { if (item.clientName.trim()) seen.current.clients.add(item.clientName.trim()); if (item.country.trim()) seen.current.countries.add(item.country.trim()) }
  const clientOptions = [...seen.current.clients].sort((a, b) => a.localeCompare(b))
  const countryOptions = [...seen.current.countries].sort((a, b) => a.localeCompare(b))
  const countrySuggestions = useMemo(() => [...new Set([...items.map((item) => item.country.trim()).filter(Boolean), ...COUNTRY_NAMES])], [items])
  const owners = useMemo(() => {
    const list = ownerChoices(store.users)
    for (const entry of summary.byOwner) if (!list.some((owner) => owner.id === entry.id)) list.push({ id: entry.id, name: entry.name })
    return list.sort((a, b) => a.name.localeCompare(b.name))
  }, [store.users, summary.byOwner])

  // A new row is on top; its Client name gets the cursor as soon as the row is drawn.
  useEffect(() => {
    if (!focusId) return
    const box = clientBoxes.current[focusId]
    if (box) { box.focus(); setFocusId(null) }
  }, [focusId, rows])

  const addRow = async (body?: RequirementPatch) => {
    setCreating(true)
    try {
      setView((current) => (current === 'closed' || current === 'dueSoon' || current === 'overdue' ? 'all' : current))
      const id = await data.create(body)
      if (id) setFocusId(id)
    } catch (failure) {
      notify(failure instanceof Error ? failure.message : 'The new row could not be saved. Please try again.', 'warn')
    } finally { setCreating(false) }
  }

  const exportFile = async () => {
    setExporting(true)
    try { await downloadRequirements(sent, 'xlsx') }
    catch (failure) { notify(failure instanceof Error ? failure.message : 'The file could not be created. Please try again.', 'warn') }
    finally { setExporting(false) }
  }

  const keep = (id: string, patch: RequirementPatch) => { void data.save(id, patch) }
  /** Active -> Closed asks why first; Closed -> Active reopens at once. */
  const flip = (item: RequirementItem) => { if (item.status === 'active') setDialog({ kind: 'close', id: item.id }); else keep(item.id, { status: flipStatus(item.status) }) }
  const close = (id: string, reason: ClosedReason) => { setDialog(null); keep(id, reason ? { status: 'closed', closedReason: reason } : { status: 'closed' }) }

  const dialogItem = dialog && 'id' in dialog ? items.find((item) => item.id === dialog.id) : undefined
  // The last row the open dialog showed: if somebody else deletes it meanwhile, the details form stays open (with what was typed) and says so.
  const lastDialogItem = useRef<RequirementItem | undefined>(undefined)
  if (dialogItem) lastDialogItem.current = dialogItem
  else if (!dialog || !('id' in dialog) || lastDialogItem.current?.id !== dialog.id) lastDialogItem.current = undefined
  const detailsGone = dialog?.kind === 'details' && !dialogItem && !!lastDialogItem.current
  const rowName = (item: RequirementItem, serial: number) => item.clientName.trim() || `row ${serial}`
  const colSpan = FIXED + fields.length
  const widths = columnWidths(Math.max(0, boxWidth - 2), fields.length, {
    client: longest(items.map((item) => item.clientName)), details: longest(items.map((item) => positionsSummary(item))),
    country: longest(items.map((item) => item.country)), owner: longest(owners.map((owner) => owner.name)),
  })
  const narrowed = filterCount(filters) > 0 || filters.q !== ''
  const filtering = narrowed || view !== 'all'
  const weekStart = daysBefore(today, 6)

  const cards = [
    { label: 'Active requirements', value: summary.active, detail: 'open now', icon: ClipboardList, tone: 'blue' as const, active: view === 'active', onSelect: () => setView(view === 'active' ? 'all' : 'active') },
    { label: 'Closed', value: summary.closed, detail: 'filled, cancelled or expired', icon: Lock, tone: 'violet' as const, active: view === 'closed', onSelect: () => setView(view === 'closed' ? 'all' : 'closed') },
    { label: 'Added this week', value: summary.addedThisWeek, detail: `since ${isoToText(weekStart)}`, icon: CalendarPlus, tone: 'green' as const, active: filters.from === weekStart && !filters.to, onSelect: () => setFilters((current) => (current.from === weekStart && !current.to ? { ...current, from: '' } : { ...current, from: weekStart, to: '' })) },
    { label: 'Due in 7 days', value: summary.dueSoon, detail: 'deadline within a week', icon: Clock3, tone: 'amber' as const, active: view === 'dueSoon', onSelect: () => setView(view === 'dueSoon' ? 'all' : 'dueSoon') },
    { label: 'Overdue', value: summary.overdue, detail: 'deadline has passed', icon: AlertTriangle, tone: 'red' as const, active: view === 'overdue', onSelect: () => setView(view === 'overdue' ? 'all' : 'overdue') },
  ]

  const downloadForm = async (item: RequirementItem) => {
    try { await downloadRequirementForm(item.id, item.clientName) }
    catch (failure) { notify(failure instanceof Error ? failure.message : 'The form could not be created. Please try again.', 'warn') }
  }

  const rowMenu = (item: RequirementItem): RowMenuItem[] => [
    { label: 'History', icon: History, onSelect: () => setDialog({ kind: 'history', id: item.id }) },
    { label: 'Download form (Excel)', icon: FileSpreadsheet, onSelect: () => { void downloadForm(item) } },
    { label: 'Duplicate requirement', icon: Copy, onSelect: () => { void addRow(duplicateBody(item)) } },
    ...(admin ? [{ label: 'Delete', icon: Trash2, tone: 'danger' as const, separated: true, onSelect: () => setDialog({ kind: 'delete-row', id: item.id }) }] : []),
  ]

  const actions = <>
    <Button variant="secondary" icon={<Settings2 size={16} />} onClick={() => setManaging(true)} disabled={Boolean(error && items.length === 0)}>Manage fields</Button>
    <Button variant="secondary" icon={exporting ? <Loader2 size={16} className="jr-spin" /> : <FileSpreadsheet size={16} />} onClick={() => void exportFile()} disabled={exporting || Boolean(error && items.length === 0)}>Export Excel</Button>
    <Button variant="primary" icon={<Plus size={16} />} onClick={() => void addRow()} disabled={creating || loading || Boolean(error)}>New requirement</Button>
  </>

  return (
    <div className="jr">
      <PageHeader title="Job requirements" actions={actions} />

      <div className="jr-cards"><SummaryCards items={cards} /></div>

      <section className="jr-panel" aria-label="Job requirements">
        <div className="jr-toolbar">
          <SearchInput className="jr-search" value={typed} onChange={setTyped} placeholder="Search client, country or position" />
          <button type="button" className="jr-addfield-phone" onClick={() => setDialog({ kind: 'field' })}><Plus size={14} aria-hidden="true" />Add field</button>
          <div className="jr-tabs" role="tablist" aria-label="Status">
            {STATUS_TABS.map((entry: { key: StatusFilter; label: string }) => (
              <button key={entry.key} type="button" role="tab" aria-selected={view === entry.key} className={cn('jr-tab', view === entry.key && 'is-on')} onClick={() => setView(entry.key)}>
                {entry.label}<span className="jr-tab-count">{counts[entry.key]}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="jr-filters" role="group" aria-label="Filters">
          <label className="jr-filter"><span>Client</span>
            <Select compact aria-label="Filter by client" value={filters.client} onChange={(event) => setFilter('client', event.target.value)}>
              <option value="">All clients</option>
              {clientOptions.map((name) => <option key={name} value={name}>{name}</option>)}
            </Select>
          </label>
          <label className="jr-filter"><span>Country</span>
            <Select compact aria-label="Filter by country" value={filters.country} onChange={(event) => setFilter('country', event.target.value)}>
              <option value="">All countries</option>
              {countryOptions.map((name) => <option key={name} value={name}>{name}</option>)}
            </Select>
          </label>
          <label className="jr-filter"><span>Assigned to</span>
            <Select compact aria-label="Filter by assigned person" value={filters.owner} onChange={(event) => setFilter('owner', event.target.value)}>
              <option value="">Everyone</option>
              {owners.map((owner) => <option key={owner.id} value={owner.id}>{owner.name}</option>)}
            </Select>
          </label>
          <div className="jr-filter jr-filter-dates"><span>DOC between</span>
            <div className="jr-daterange">
              <DatePicker compact aria-label="DOC from" value={filters.from} max={filters.to || undefined} placeholder="From" onChange={(value) => setFilter('from', value)} />
              <DatePicker compact aria-label="DOC to" value={filters.to} min={filters.from || undefined} placeholder="To" onChange={(value) => setFilter('to', value)} />
            </div>
          </div>
          {(filtering || typed) && <Button size="sm" variant="ghost" icon={<X size={14} />} onClick={clearFilters}>Clear filters</Button>}
          {VIEW_NAMES[view] && <span className="jr-viewchip">Showing: {VIEW_NAMES[view]}</span>}
        </div>

        {error && items.length === 0 ? (
          <div className="jr-state" role="alert">
            <EmptyState title="The job requirements could not be loaded" description={error} action={<Button variant="primary" icon={<RefreshCw size={15} />} onClick={() => void data.reload({ fresh: true })}>Try again</Button>} />
          </div>
        ) : !loading && items.length === 0 && !narrowed ? (
          <div className="jr-state"><EmptyState icon={<ClipboardList size={20} strokeWidth={1.6} />} title="No job requirements yet. Press + New requirement." action={<Button variant="primary" icon={<Plus size={16} />} onClick={() => void addRow()} disabled={creating}>New requirement</Button>} /></div>
        ) : (
          <div className="jr-scroll" ref={boxRef} role="region" aria-label="Job requirements table. Scroll sideways for more columns" tabIndex={0}>
            <table className="jr-table" style={{ width: widths.total }} aria-busy={loading || undefined}>
              <colgroup>
                <col style={{ width: FIXED_WIDTHS.serial }} /><col style={{ width: FIXED_WIDTHS.doc }} /><col style={{ width: widths.client }} /><col style={{ width: widths.country }} />
                <col style={{ width: widths.owner }} /><col style={{ width: widths.details }} /><col style={{ width: FIXED_WIDTHS.ad }} /><col style={{ width: FIXED_WIDTHS.status }} />
                {fields.map((field) => <col key={field.id} style={{ width: CUSTOM_WIDTH }} />)}
                <col style={{ width: FIXED_WIDTHS.actions }} />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">S.No</th>
                  <th scope="col">DOC</th>
                  <th scope="col" className="is-left">Client name</th>
                  <th scope="col">Country</th>
                  <th scope="col">Assigned to</th>
                  <th scope="col">Job positions</th>
                  <th scope="col">Ad</th>
                  <th scope="col">Status</th>
                  {fields.map((field) => (
                    <th key={field.id} scope="col">
                      <ColumnHead field={field} menu={
                        <RowMenu label={`${field.label} column menu`} items={[
                          { label: 'Rename', icon: Pencil, onSelect: () => setDialog({ kind: 'field', field }) },
                          ...(admin ? [{ label: 'Delete field', icon: Trash2, tone: 'danger' as const, separated: true, onSelect: () => setDialog({ kind: 'delete-field', field }) }] : []),
                        ]} />
                      } />
                    </th>
                  ))}
                  <th scope="col" className="jr-th-add">
                    <button type="button" className="jr-addfield" onClick={() => setDialog({ kind: 'field' })}><Plus size={14} aria-hidden="true" />Add field</button>
                  </th>
                </tr>
              </thead>
              <tbody>
                {loading && items.length === 0
                  ? Array.from({ length: 6 }, (_, index) => (
                    <tr key={index} className="jr-skeleton" aria-hidden="true">
                      {Array.from({ length: colSpan }, (__, cell) => <td key={cell}><span /></td>)}
                    </tr>
                  ))
                  : rows.length === 0
                    ? <tr className="jr-none"><td colSpan={colSpan}>{filtering ? 'No job requirements match these filters.' : 'No job requirements yet.'}</td></tr>
                    : rows.map(({ serial, item }) => {
                      const name = rowName(item, serial)
                      const save = data.rowSave[item.id]
                      return (
                        <tr key={item.id} className={cn('jr-row', save?.state === 'error' && 'has-error')} title={whoWhen(item) || undefined}>
                          <td data-label="S.No" className="jr-serial">{serial}</td>
                          <td data-label="DOC">
                            <InlineCell saved={item.docDate ?? ''} show={isoToText} read={dateRead} shape={shapeDateText} picker placeholder="dd/mm/yyyy" inputMode="numeric" maxLength={10}
                              label={`DOC of ${name}, as dd/mm/yyyy`} onSave={(iso) => keep(item.id, { docDate: iso || null })} />
                          </td>
                          <td data-label="Client name" className="is-left">
                            <InlineCell multiline saved={item.clientName} show={identity} read={textRead} placeholder="Client name" maxLength={200} label={`Client name of ${name}`}
                              inputRef={(node) => { clientBoxes.current[item.id] = node }}
                              onSave={(value) => keep(item.id, { clientName: value })} />
                          </td>
                          <td data-label="Country">
                            <InlineCell multiline centered saved={item.country} show={identity} read={textRead} placeholder="Country" maxLength={100} options={countrySuggestions} label={`Country of ${name}`}
                              onSave={(value) => keep(item.id, { country: value })} />
                          </td>
                          <td data-label="Assigned to">
                            <OwnersCell item={item} choices={ownerChoices(store.users, item)} label={name} onSave={(ids) => keep(item.id, { ownerIds: ids })} />
                          </td>
                          <td data-label="Job positions"><DetailsButton item={item} today={today} onOpen={() => setDialog({ kind: 'details', id: item.id })} /></td>
                          <td data-label="Ad">
                            <AdCell item={item} onUpload={(file) => data.uploadAd(item.id, file)} onRemove={() => data.removeAd(item.id)} onError={(message) => notify(message, 'warn')} />
                          </td>
                          <td data-label="Status"><StatusToggle item={item} onFlip={() => flip(item)} /></td>
                          {fields.map((field) => (
                            <td key={field.id} data-label={field.label}>
                              <CustomCell field={field} item={item} label={`${field.label} of ${name}`} onSave={(value) => keep(item.id, { custom: { [field.id]: value } })} />
                            </td>
                          ))}
                          <td data-label="" className="jr-actions-cell">
                            <span className="jr-actions">
                              <SaveMark save={save} onRetry={() => void data.retry(item.id)} />
                              <RowMenu label={`Actions for ${name}`} items={rowMenu(item)} />
                            </span>
                            {save?.state === 'error' && <span className="jr-row-error" role="alert">{save.message}</span>}
                          </td>
                        </tr>
                      )
                    })}
              </tbody>
            </table>
          </div>
        )}

        {items.length > 0 && (
          <footer className="jr-foot">
            <span>{items.length < data.total ? `Showing ${items.length} of ${data.total}` : `${data.total} ${data.total === 1 ? 'requirement' : 'requirements'}`}</span>
            {items.length < data.total && <Button size="sm" onClick={() => void data.loadMore()} disabled={data.loadingMore}>{data.loadingMore ? 'Loading...' : 'Load more'}</Button>}
            {error && <span className="jr-foot-error" role="alert">{error}</span>}
          </footer>
        )}
      </section>

      {dialog?.kind === 'details' && (dialogItem ?? lastDialogItem.current) && (
        <JobRequirementForm item={(dialogItem ?? lastDialogItem.current) as RequirementItem} options={data.options} onCreateOption={(kind, label) => data.createOption(kind, label)} onManage={() => setManaging(true)} gone={detailsGone} onClose={() => setDialog(null)} onSave={(patch) => data.save((dialogItem ?? lastDialogItem.current as RequirementItem).id, patch)} />
      )}
      {managing && (
        <FieldManagerDialog options={data.options} canDelete={admin} onClose={() => setManaging(false)}
          onCreate={(kind, label, standard) => data.createOption(kind, label, standard)} onUpdate={(id, body) => data.updateOption(id, body)} onDelete={(id) => data.deleteOption(id)} />
      )}
      {dialog?.kind === 'close' && dialogItem && (
        <CloseDialog item={dialogItem} onClose={() => setDialog(null)} onPick={(reason) => close(dialogItem.id, reason)} />
      )}
      {dialog?.kind === 'history' && dialogItem && <HistoryDrawer item={dialogItem} onClose={() => setDialog(null)} />}
      {dialog?.kind === 'field' && (
        <FieldDialog field={dialog.field} fields={fields} onClose={() => setDialog(null)}
          onSave={async (label, type) => { if (dialog.field) await data.renameField(dialog.field.id, label); else await data.addField(label, type) }} />
      )}
      {dialog?.kind === 'delete-row' && dialogItem && (
        <ConfirmDialog title="Delete this requirement?" action="Delete requirement" onClose={() => setDialog(null)}
          body={`${dialogItem.clientName.trim() || 'This blank row'} and everything typed in it will be removed for everyone. This cannot be undone.`}
          onConfirm={async () => { await data.remove(dialogItem.id); notify('Requirement deleted') }} />
      )}
      {dialog?.kind === 'delete-field' && (
        <ConfirmDialog title={`Delete the "${dialog.field.label}" column?`} action="Delete field" onClose={() => setDialog(null)}
          body="The column and every value typed in it will be removed for everyone. This cannot be undone."
          onConfirm={async () => { await data.deleteField(dialog.field.id); notify('Column deleted') }} />
      )}
    </div>
  )
}
