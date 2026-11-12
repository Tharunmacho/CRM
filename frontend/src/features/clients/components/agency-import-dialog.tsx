import { useRef, useState } from 'react'
import { CheckCircle2, FileSpreadsheet, Loader2, RefreshCw, Upload } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { Button, Field, Input, Select } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { DataTable, type Column } from '@/shared/ui/table'
import { agencyImportApi } from '../agency-import-api'
import {
  AGENCY_IMPORT_FIELDS, AGENCY_IMPORT_STATUS, agencyImportFileError, agencyImportReadyCount, sameAgencyImportOptions,
  type AgencyImportOptions, type AgencyImportPreview, type AgencyImportResult, type AgencyImportRow,
} from '../agency-import-model'
import '@/styles/agency-import.css'

function ImportRows({ preview }: { preview: AgencyImportPreview }) {
  const columns: Column<AgencyImportRow>[] = [
    { key: 'row', header: 'Row', width: '6%', render: row => row.rowNumber },
    { key: 'agency', header: 'Agency / contact', width: '25%', align: 'left', render: row => <div className="ai-cell">
      <strong>{row.values.agencyName || '—'}</strong>
      {row.values.name && row.values.name !== row.values.agencyName && <span>{row.values.name}</span>}
      {row.values.phone && <span>{row.values.phone}</span>}{row.values.email && <span>{row.values.email}</span>}
    </div> },
    { key: 'place', header: 'Location / notes', width: '34%', align: 'left', render: row => <div className="ai-cell">
      {[row.values.place, row.values.state].filter(Boolean).length > 0 && <strong>{[row.values.place, row.values.state].filter(Boolean).join(', ')}</strong>}
      {row.values.address && <span>{row.values.address}</span>}{row.values.notes && <span>{row.values.notes}</span>}
      {!row.values.place && !row.values.state && !row.values.address && !row.values.notes && <span className="ai-muted">—</span>}
    </div> },
    { key: 'status', header: 'Import result', width: '35%', align: 'left', render: row => <div className="ai-cell">
      <span className={`ai-status is-${row.status}`}>{AGENCY_IMPORT_STATUS[row.status]}</span><span>{row.message}</span>
    </div> },
  ]
  return <DataTable compact pageSize={10} rows={preview.rows} rowKey={row => String(row.rowNumber)} columns={columns} resetPageKey={preview.previewHash} />
}

/** A read-only preview followed by an explicit, hash-bound import. Failed confirmations must be re-previewed. */
export function AgencyImportDialog({ onClose, onImported }: { onClose: () => void; onImported: () => void }) {
  const store = useStore()
  const input = useRef<HTMLInputElement>(null)
  const flight = useRef(false)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<AgencyImportPreview | null>(null)
  const [options, setOptions] = useState<AgencyImportOptions>({ mapping: {}, headerRow: 1, sheetName: '' })
  const [result, setResult] = useState<AgencyImportResult | null>(null)
  const [busy, setBusy] = useState<'preview' | 'confirm' | null>(null)
  const [error, setError] = useState('')
  const [refreshRequired, setRefreshRequired] = useState(false)
  const [dragging, setDragging] = useState(false)
  const changed = !!preview && !sameAgencyImportOptions(preview, options)
  const ready = agencyImportReadyCount(preview)
  const needsPreview = changed || refreshRequired

  const read = async (chosen: File, selections?: Partial<AgencyImportOptions>) => {
    if (flight.current) return
    const problem = agencyImportFileError(chosen)
    if (problem) { setError(problem); return }
    flight.current = true; setBusy('preview'); setError(''); setRefreshRequired(true)
    try {
      // A different worksheet can have entirely different headers and column order.
      const requested = selections?.sheetName && preview && selections.sheetName !== preview.sheetName ? { sheetName: selections.sheetName } : selections
      const response = await agencyImportApi.preview(chosen, requested)
      const next = { ...response, sheetName: response.sheetName ?? '' }
      setFile(chosen); setPreview(next); setOptions({ mapping: next.mapping, headerRow: next.headerRow, sheetName: next.sheetName ?? '' })
      setResult(null); setRefreshRequired(false)
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'We could not preview this file. Please try again.') }
    finally { flight.current = false; setBusy(null) }
  }

  const choose = (files: FileList | null) => {
    if (flight.current || !files?.length) return
    if (files.length !== 1) { setError('Choose one Excel or CSV file at a time.'); return }
    const chosen = files[0], problem = agencyImportFileError(chosen)
    if (problem) { setError(problem); return }
    setFile(chosen); setPreview(null); setResult(null); setOptions({ mapping: {}, headerRow: 1, sheetName: '' })
    void read(chosen)
  }

  const confirm = async () => {
    if (flight.current || !file || !preview || needsPreview || !preview.canConfirm || ready === 0) return
    flight.current = true; setBusy('confirm'); setError('')
    try {
      const saved = await agencyImportApi.confirm(file, preview)
      setResult(saved)
      store.notify(`${saved.created} ${saved.created === 1 ? 'agency' : 'agencies'} imported${saved.contactsAdded ? ` · ${saved.contactsAdded} contacts added` : ''}`)
      onImported()
    } catch (cause) {
      setRefreshRequired(true)
      setError(`${cause instanceof Error ? cause.message : 'We could not confirm the import.'} Refresh the preview before trying again.`)
    } finally { flight.current = false; setBusy(null) }
  }

  const close = () => { if (!flight.current) onClose() }
  return <Dialog open onClose={close} title="Import agencies" width="xl" scroll="inside"
    description="Upload an Excel or CSV file, review the names and contacts, then confirm."
    icon={<FileSpreadsheet size={23} />}
    footer={result ? <Button variant="primary" onClick={close}>Done</Button> : <>
      <Button variant="ghost" disabled={!!busy} onClick={close}>Cancel</Button>
      {file && (!preview || needsPreview) && <Button disabled={!!busy} onClick={() => void read(file, preview ? options : undefined)}><RefreshCw size={15} />Refresh preview</Button>}
      <Button variant="primary" disabled={!!busy || !preview?.canConfirm || needsPreview || ready === 0} onClick={() => void confirm()}>
        {busy === 'confirm' ? <><Loader2 size={16} className="animate-spin" />Importing…</> : 'Confirm import'}
      </Button>
    </>}>
    <div className="agency-import">
      {error && <p className="ai-error" role="alert">{error}</p>}
      {result ? <section className="ai-result" role="status">
        <CheckCircle2 size={36} /><h3>Import complete</h3>
        <div className="ai-summary">
          <span><strong>{result.created}</strong> agencies created</span><span><strong>{result.contactsAdded}</strong> contacts added</span>
          <span><strong>{result.skipped}</strong> rows skipped</span><span><strong>{result.conflicts + result.invalid}</strong> rows need correction</span>
        </div>
        <p>The agency directory is updated. Duplicates were skipped. Correct any conflicting or invalid rows before importing them again.</p>
        <Button onClick={() => { setResult(null); setPreview(null); setFile(null); setError('') }}>Import another file</Button>
      </section> : <>
        <section className={`ai-upload${dragging ? ' is-dragging' : ''}`} aria-label="Agency spreadsheet"
          onDragOver={event => { event.preventDefault(); if (!busy) setDragging(true) }}
          onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); choose(event.dataTransfer.files) }}>
          <FileSpreadsheet size={27} aria-hidden="true" />
          <div><strong>{file?.name || 'Choose your agency list'}</strong><p>Excel (.xlsx) or UTF-8 CSV · up to 5 MB and 2,000 rows</p></div>
          <input ref={input} type="file" accept=".xlsx,.csv" className="sr-only" aria-label="Choose agency spreadsheet" disabled={!!busy}
            onChange={event => { choose(event.target.files); event.target.value = '' }} />
          <Button disabled={!!busy} onClick={() => input.current?.click()}><Upload size={16} />{file ? 'Change file' : 'Choose file'}</Button>
        </section>
        {busy === 'preview' && <p className="ai-progress" role="status"><Loader2 size={17} className="animate-spin" />Reading your sheet and checking for duplicates…</p>}
        {!preview && !busy && <p className="ai-help">Agency name or contact person is enough. A contact named Tharun becomes “Tharun Agency” when no agency name is supplied. Place / City and Address are separate, optional details.</p>}
        {preview && <>
          <details className="ai-mapping"><summary>Match spreadsheet columns</summary>
            <fieldset disabled={!!busy}>
              <div className="ai-sheet-options">
                {preview.sheets.length > 1 && <Field label="Worksheet"><Select aria-label="Worksheet" value={options.sheetName} onChange={event => {
                  const sheetName = event.target.value
                  setOptions(previous => ({ ...previous, sheetName }))
                  if (file) void read(file, { sheetName })
                }}>{preview.sheets.map(sheet => <option key={sheet} value={sheet}>{sheet}</option>)}</Select></Field>}
                <Field label="Header row"><Input type="number" min={0} max={20} value={options.headerRow} onChange={event => setOptions(previous => ({ ...previous, headerRow: Number(event.target.value) }))} /><small>Choose a row from 1 to 20, or 0 for no header.</small></Field>
              </div>
              <div className="ai-map-grid">{AGENCY_IMPORT_FIELDS.map(([key, label]) => <Field key={key} label={label}>
                <Select aria-label={`${label} column`} value={options.mapping[key] ?? ''} onChange={event => {
                  const value = event.target.value
                  setOptions(previous => { const mapping = { ...previous.mapping }; if (value === '') delete mapping[key]; else mapping[key] = Number(value); return { ...previous, mapping } })
                }}><option value="">Not in this sheet</option>{preview.columns.map(column => <option key={column.index} value={column.index}
                  disabled={Object.entries(options.mapping).some(([field, index]) => field !== key && index === column.index)}>
                  {column.index + 1}. {column.label || 'Unnamed column'}{column.sample?.[0] ? ` · ${column.sample[0].slice(0, 45)}` : ''}
                </option>)}</Select>
              </Field>)}</div>
              <p className="ai-help">Each column is used once. Choose “Not in this sheet” to free a column before assigning it elsewhere.</p>
              <div className="ai-mapping-actions"><Button disabled={!!busy} onClick={() => file && void read(file, options)}><RefreshCw size={15} />Update preview</Button>
                <Button variant="ghost" disabled={!!busy} onClick={() => file && void read(file, { sheetName: options.sheetName, headerRow: options.headerRow })}>Detect columns again</Button></div>
            </fieldset>
          </details>
          {needsPreview && <p className="ai-help" role="status">Refresh the preview to review your current selections before confirming.</p>}
          <div className="ai-summary" aria-label="Import preview totals">
            <span><strong>{preview.counts.create}</strong> new agencies</span><span><strong>{preview.counts.add_contact}</strong> contacts to add</span>
            <span><strong>{preview.counts.duplicate}</strong> duplicates skipped</span><span><strong>{preview.counts.conflict + preview.counts.invalid}</strong> rows need correction</span>
          </div>
          <p className="ai-help">Confirming creates the new agencies and adds the listed contacts to matching agencies. Duplicates, conflicts, invalid rows and section headings are skipped.</p>
          {!preview.canConfirm && <p className="ai-help" role="status">There are no rows ready to import. Review the results below or choose another file.</p>}
          <ImportRows preview={preview} />
        </>}
      </>}
    </div>
  </Dialog>
}
