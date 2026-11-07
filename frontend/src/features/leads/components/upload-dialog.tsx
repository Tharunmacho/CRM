import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { AlertTriangle, ArrowLeft, CheckCircle2, CloudUpload, Download, FileSpreadsheet, Loader2 } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import type { User } from '@/shared/model/types'
import { Dialog } from '@/shared/ui/overlay'
import { Button, Input, Select } from '@/shared/ui/primitives'
import { DbTable, type DbColumn } from '@/features/candidates/components/db-table'
import { CountryPills } from '@/features/candidates/components/data-bank-columns'
import { downloadBatchReport, importLeads } from '../lead-api'
import {
  CONFIDENCE_LABEL, EXTRA_FIELD, LEAD_ACCEPT, LEAD_FIELDS, LEAD_MAX_BYTES, applyOverrides, confidenceKind, countsOf, duplicateReason, duplicateTargets, fieldLabel, formatBytes,
  REPORT_BUTTON, allNewLine, invalidReason, leadFileError, resultSummary, sampleValues, setMappingOverride, splitMappings,
  type ColumnInfo, type DuplicateRow, type ImportCounts, type ImportResponse, type InvalidRow, type Lead,
} from '../lead-model'
import { CreatedCell, PositionPill } from './lead-columns'

type Stage = 'choose' | 'checking' | 'preview' | 'importing' | 'result'
type PreviewTab = 'rows' | 'ignored' | 'invalid'
type SampleRow = NonNullable<ImportResponse['sample']>[number]

interface Props {
  open: boolean
  onClose: () => void
  users: User[]
  /** Called after a real import so the table refreshes and highlights the new rows. */
  onImported: (result: ImportResponse) => void
  /** "View new leads": close the dialog and filter the table to the batch. */
  onViewBatch: (batchId: string) => void
}

const ASSIGN_KEEP = ''
const ASSIGN_EVEN = 'auto'
const PREVIEW_ROWS = 20

/** Drag-and-drop zone for the lead file; a click opens the file picker as well. */
function DropZone({ onFile }: { onFile: (file: File) => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const drop = (event: DragEvent) => { event.preventDefault(); setOver(false); const file = event.dataTransfer.files?.[0]; if (file) onFile(file) }
  return (
    <div className={cn('ld-drop', over && 'is-over')} onDragOver={(event) => { event.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)} onDrop={drop}>
      <span className="ld-drop-icon" aria-hidden="true"><CloudUpload size={26} /></span>
      <p className="ld-drop-title">Drop your lead sheet here</p>
      <p className="ld-drop-sub">or</p>
      <Button type="button" variant="primary" onClick={() => input.current?.click()}>Choose a file</Button>
      <input ref={input} type="file" accept={LEAD_ACCEPT} className="sr-only" tabIndex={-1} aria-label="Choose a lead file" onChange={(event) => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ''; if (file) onFile(file) }} />
      <ul className="ld-drop-facts">
        <li>CSV, TSV, TXT or Excel (.xlsx)</li>
        <li>Up to {formatBytes(LEAD_MAX_BYTES)}</li>
        <li>Meta (Facebook / Instagram) and Google lead exports work as they are</li>
      </ul>
    </div>
  )
}

function Counts({ counts }: { counts: ImportCounts }) {
  const cards: { label: string; value: number; note: string; tone: string }[] = [
    { label: 'Rows in file', value: counts.total, note: 'people found', tone: '' },
    { label: 'New', value: counts.new, note: 'will be added', tone: 'ok' },
    { label: 'Already stored', value: counts.duplicate, note: 'will be ignored', tone: counts.duplicate ? 'warn' : '' },
    { label: 'Invalid', value: counts.invalid, note: 'no usable phone or email', tone: counts.invalid ? 'bad' : '' },
    { label: 'In the Data Bank', value: counts.existingCandidate, note: 'already candidates', tone: counts.existingCandidate ? 'info' : '' },
  ]
  return (
    <div className="ld-counts" role="group" aria-label="What the file contains">
      {cards.map((card) => (
        <div key={card.label} className={cn('ld-count', card.tone && `is-${card.tone}`)}>
          <span>{card.label}</span>
          <strong className="tnum">{card.value.toLocaleString()}</strong>
          <small>{card.note}</small>
        </div>
      ))}
    </div>
  )
}

function MappingTable({ columns, sample, overrides, onChange, busy }: { columns: ColumnInfo[]; sample: Record<string, unknown>[]; overrides: Record<string, string | null>; onChange: (column: ColumnInfo, field: string) => void; busy: boolean }) {
  const applied = applyOverrides(columns, overrides)
  const { mapped, extra } = splitMappings(applied)
  const duplicates = duplicateTargets(columns, overrides)
  const known = new Set(LEAD_FIELDS.map((field) => field.value))
  const extraOptions = [...new Set(columns.map((column) => column.field).filter((field): field is string => !!field && !known.has(field)))]
  const row = (column: ColumnInfo) => {
    const changed = column.header in overrides
    const kind = column.field ? confidenceKind(column.confidence) : null
    const samples = sampleValues(sample, column.field)
    return (
      <tr key={column.header}>
        <td className="ld-map-col" title={column.header}><span>{column.header}</span></td>
        <td className="ld-map-sample" title={samples.join(' · ')}>{samples.join(' · ') || <span className="db-muted">–</span>}</td>
        <td className="ld-map-to">
          <Select aria-label={`Where "${column.header}" goes`} value={column.field ?? EXTRA_FIELD} disabled={busy} onChange={(event) => onChange(columns.find((item) => item.header === column.header) as ColumnInfo, event.target.value)}>
            <option value={EXTRA_FIELD}>Keep as extra answer</option>
            {LEAD_FIELDS.map((field) => <option key={field.value} value={field.value}>{field.label}</option>)}
            {extraOptions.map((field) => <option key={field} value={field}>{fieldLabel(field)}</option>)}
          </Select>
        </td>
        <td className="ld-map-badge">
          {changed ? <span className="ld-badge is-you">Changed</span> : kind ? <span className={cn('ld-badge', `is-${kind}`)}>{CONFIDENCE_LABEL[kind]}</span> : <span className="ld-badge is-extra">Extra</span>}
        </td>
      </tr>
    )
  }
  return (
    <div className="ld-map">
      <div className="ld-map-scroll">
        <table>
          <thead><tr><th>Column in your file</th><th>Example</th><th>Goes into</th><th>Match</th></tr></thead>
          <tbody>{[...mapped, ...extra].map(row)}</tbody>
        </table>
      </div>
      {duplicates.length > 0 && <p className="ld-warn" role="alert"><AlertTriangle size={14} aria-hidden="true" />Two columns go into {duplicates.map(fieldLabel).join(', ')}. Only one is kept.</p>}
      <p className="ld-map-foot">{mapped.length} {mapped.length === 1 ? 'column goes' : 'columns go'} into lead fields{extra.length > 0 ? `; ${extra.length} kept as extra answers (shown in each lead's detail)` : ''}. Wrong? Change where a column goes and the check runs again.</p>
    </div>
  )
}

const contactCell = (phone?: string | null, email?: string | null) => (
  <span className="db-2l"><span className="ident db-num">{phone || '–'}</span>{email && <small className="db-ellip">{email}</small>}</span>
)

const previewColumns: DbColumn<SampleRow>[] = [
  { key: 'created', header: 'Created', width: 68, render: (l) => <CreatedCell lead={l as Lead} /> },
  { key: 'name', header: 'Name', width: 130, render: (l) => <span className="db-2l is-name"><strong className="db-clamp">{l.name || 'Unnamed'}</strong>{l.gender && <small>{l.gender}</small>}</span> },
  { key: 'phone', header: 'Phone / Email', width: 176, render: (l) => contactCell(l.phone, l.email) },
  { key: 'position', header: 'Job applied', width: 140, render: (l) => <PositionPill value={l.positionApplied} /> },
  { key: 'city', header: 'City / State', width: 108, render: (l) => <span className="db-2l"><strong className="db-clamp">{l.city || '–'}</strong>{l.state && <small className="db-ellip">{l.state}</small>}</span> },
  { key: 'campaign', header: 'Campaign', width: 130, render: (l) => <span className="db-2l"><span className="db-clamp">{l.campaignName || '–'}</span>{l.platform && <small>{l.platform}</small>}</span> },
  { key: 'country', header: 'Country interest', width: 108, render: (l) => <CountryPills value={l.countryInterest} /> },
  {
    key: 'outcome', header: 'Result', width: 92,
    render: (l) => <span className={cn('db-tone', l.outcome === 'new' ? 'is-ok' : l.outcome === 'invalid' ? 'is-bad' : 'is-warn')}>{l.outcome === 'new' ? 'Will be added' : l.outcome === 'invalid' ? 'Invalid' : 'Ignored'}</span>,
  },
]

const duplicateColumns: DbColumn<DuplicateRow>[] = [
  { key: 'row', header: 'Row', width: 64, render: (r) => <span className="tnum db-num">{r.row}</span> },
  { key: 'name', header: 'Name', width: 160, render: (r) => <strong className="db-clamp">{r.name || 'Unnamed'}</strong> },
  { key: 'phone', header: 'Phone', width: 160, render: (r) => <span className="ident db-num">{r.phone || '–'}</span> },
  { key: 'reason', header: 'Why it was ignored', width: 280, render: (r) => <span className="db-tone is-warn">{duplicateReason(r)}</span> },
]

const invalidColumns: DbColumn<InvalidRow>[] = [
  { key: 'row', header: 'Row', width: 64, render: (r) => <span className="tnum db-num">{r.row}</span> },
  { key: 'name', header: 'Name', width: 160, render: (r) => <strong className="db-clamp">{r.name || 'Unnamed'}</strong> },
  { key: 'phone', header: 'Phone', width: 160, render: (r) => <span className="ident db-num">{r.phone || '–'}</span> },
  { key: 'reason', header: 'Problem', width: 280, render: (r) => <span className="db-tone is-bad">{invalidReason(r)}</span> },
]

export function UploadLeadsDialog({ open, onClose, users, onImported, onViewBatch }: Props) {
  const [stage, setStage] = useState<Stage>('choose')
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<ImportResponse | null>(null)
  const [overrides, setOverrides] = useState<Record<string, string | null>>({})
  const [tab, setTab] = useState<PreviewTab>('rows')
  const [error, setError] = useState('')
  const [assign, setAssign] = useState(ASSIGN_KEEP)
  const [label, setLabel] = useState('')
  const [result, setResult] = useState<ImportResponse | null>(null)
  const [reporting, setReporting] = useState(false)
  const run = useRef(0)

  const reset = useCallback(() => {
    run.current += 1
    setStage('choose'); setFile(null); setPreview(null); setOverrides({}); setTab('rows'); setError(''); setAssign(ASSIGN_KEEP); setLabel(''); setResult(null)
  }, [])
  useEffect(() => { if (!open) reset() }, [open, reset])

  const check = useCallback(async (chosen: File, mapping: Record<string, string | null>, hadPreview: boolean) => {
    const mine = ++run.current
    setStage('checking'); setError('')
    try {
      const data = await importLeads(chosen, { dryRun: true, mapping })
      if (mine !== run.current) return
      setPreview(data); setStage('preview')
    } catch (e) {
      if (mine !== run.current) return
      setError(e instanceof Error ? e.message : 'The file could not be read.')
      setStage(hadPreview ? 'preview' : 'choose')
    }
  }, [])

  const pick = (chosen: File) => {
    const invalid = leadFileError(chosen)
    if (invalid) { setFile(null); setPreview(null); setError(invalid); setStage('choose'); return }
    setFile(chosen); setOverrides({}); setPreview(null); setTab('rows')
    void check(chosen, {}, false)
  }

  const remap = (column: ColumnInfo, field: string) => {
    if (!file) return
    const next = setMappingOverride(overrides, column, field)
    setOverrides(next)
    void check(file, next, true)
  }

  const doImport = async () => {
    if (!file || !preview) return
    const mine = ++run.current
    setStage('importing'); setError('')
    try {
      const data = await importLeads(file, { dryRun: false, mapping: overrides, label, assignTo: assign || undefined })
      if (mine !== run.current) return
      setResult(data); setStage('result'); onImported(data)
    } catch (e) {
      if (mine !== run.current) return
      setError(e instanceof Error ? e.message : 'The import did not finish. Nothing was lost; you can try again.')
      setStage('preview')
    }
  }

  const report = async (batchId: string, kind: 'xlsx' | 'csv' = 'xlsx') => {
    setReporting(true); setError('')
    try { await downloadBatchReport(batchId, kind) } catch (e) { setError(e instanceof Error ? e.message : 'We could not make the report.') } finally { setReporting(false) }
  }

  const staff = useMemo(() => users.filter((user) => user.active && user.role !== 'admin'), [users])
  const busy = stage === 'checking' || stage === 'importing'
  const counts = preview ? countsOf(preview) : null
  const nothingNew = !!counts && counts.new === 0
  const resultCounts = result ? countsOf(result) : null

  const footer = stage === 'result' && result && resultCounts ? (
    <>
      <Button variant="secondary" onClick={reset}>Upload another file</Button>
      {result.batchId && <Button variant="secondary" icon={<Download className="h-4 w-4" />} disabled={reporting} onClick={() => void report(result.batchId as string)}>{reporting ? 'Preparing…' : REPORT_BUTTON}</Button>}
      {result.batchId && <Button variant="secondary" disabled={reporting} onClick={() => void report(result.batchId as string, 'csv')}>CSV</Button>}
      {result.batchId && <Button variant="primary" onClick={() => onViewBatch(result.batchId as string)}>View new leads</Button>}
    </>
  ) : stage === 'preview' || stage === 'importing' || (stage === 'checking' && preview) ? (
    <>
      <Button variant="secondary" icon={<ArrowLeft className="h-4 w-4" />} disabled={busy} onClick={reset}>Choose another file</Button>
      <Button variant="primary" disabled={busy || nothingNew} onClick={() => void doImport()}>
        {stage === 'importing' ? 'Importing…' : nothingNew ? 'Nothing new to import' : `Import ${counts?.new.toLocaleString() ?? ''} new ${counts?.new === 1 ? 'lead' : 'leads'}`}
      </Button>
    </>
  ) : (
    <Button variant="secondary" onClick={onClose}>Cancel</Button>
  )

  const sample = (preview?.sample ?? []).slice(0, PREVIEW_ROWS)
  return (
    <Dialog open={open} onClose={() => { if (stage !== 'importing') onClose() }} title="Upload leads" width="xl"
      description={stage === 'result' ? undefined : 'Add the sheet your ad platform gave you. We check it first and save nothing until you confirm. People who are already stored are skipped, so nobody is added twice.'}
      footer={footer}>
      <div className="ld-upload-body">
        {error && <p role="alert" className="ld-alert"><AlertTriangle size={16} aria-hidden="true" /><span>{error}</span></p>}

        {stage === 'choose' && <DropZone onFile={pick} />}

        {stage === 'checking' && !preview && (
          <div className="ld-busy" role="status"><Loader2 className="ld-spin" size={26} aria-hidden="true" /><p>Checking {file?.name}…</p><small>Reading the columns and looking for leads you already have.</small></div>
        )}

        {preview && file && (stage === 'preview' || stage === 'importing' || stage === 'checking') && (
          <div className={cn('ld-preview-stack', busy && 'is-busy')} aria-busy={busy || undefined}>
            <div className="ld-file">
              <span className="ld-file-icon" aria-hidden="true"><FileSpreadsheet size={20} /></span>
              <div className="ld-file-text">
                <strong title={file.name}>{file.name}</strong>
                <small>{formatBytes(file.size)}{preview.format ? ` · ${preview.format.toUpperCase()}` : ''}{preview.encoding ? ` · ${preview.encoding}` : ''}{preview.delimiter ? ` · ${preview.delimiter === '\t' ? 'tab' : preview.delimiter === ',' ? 'comma' : preview.delimiter === ';' ? 'semicolon' : preview.delimiter} separated` : ''}</small>
              </div>
              {stage === 'checking' && <span className="ld-file-state"><Loader2 className="ld-spin" size={15} aria-hidden="true" />Checking again…</span>}
            </div>

            {preview.sameFileBefore && (
              <p className="ld-warn is-block" role="alert"><AlertTriangle size={16} aria-hidden="true" /><span><strong>This exact file was uploaded before</strong> ({new Date(preview.sameFileBefore.uploadedAt).toLocaleDateString()}). You can still import it, but it will add no new leads.</span></p>
            )}

            {counts && <Counts counts={counts} />}

            <section className="ld-section ld-options">
              <label className="ld-opt">
                <span>Assign new leads to</span>
                <Select aria-label="Assign new leads to" value={assign} disabled={busy} onChange={(event) => setAssign(event.target.value)}>
                  <option value={ASSIGN_KEEP}>Nobody yet (assign later)</option>
                  <option value={ASSIGN_EVEN}>Distribute evenly between available staff</option>
                  {staff.map((user) => <option key={user.id} value={user.id}>{user.name}{user.onLeave ? ' (on leave)' : ''}</option>)}
                </Select>
              </label>
              <label className="ld-opt">
                <span>Campaign name for rows without one <em>(optional)</em></span>
                <Input value={label} maxLength={80} disabled={busy} placeholder="e.g. Dubai chef ads, week 45" onChange={(event) => setLabel(event.target.value)} />
              </label>
            </section>

            <section className="ld-section">
              <h3>How your columns were read</h3>
              <MappingTable columns={preview.columns} sample={preview.sample ?? []} overrides={overrides} onChange={remap} busy={busy} />
            </section>

            <section className="ld-section">
              <div className="ld-tabs" role="tablist" aria-label="Preview">
                <button type="button" role="tab" aria-selected={tab === 'rows'} className={cn(tab === 'rows' && 'is-on')} onClick={() => setTab('rows')}>First {Math.min(PREVIEW_ROWS, sample.length)} rows</button>
                <button type="button" role="tab" aria-selected={tab === 'ignored'} className={cn(tab === 'ignored' && 'is-on')} onClick={() => setTab('ignored')}>Ignored rows <span className="tnum">{preview.rowsDuplicate}</span></button>
                {preview.rowsInvalid > 0 && <button type="button" role="tab" aria-selected={tab === 'invalid'} className={cn(tab === 'invalid' && 'is-on')} onClick={() => setTab('invalid')}>Invalid <span className="tnum">{preview.rowsInvalid}</span></button>}
              </div>
              <div className="db ld-preview-table">
                {tab === 'rows' && (sample.length
                  ? <DbTable<SampleRow> variant="grid" label="First rows of the file" rows={sample} columns={previewColumns} rowKey={(l) => `${l.externalId ?? ''}-${l.phone ?? ''}-${l.email ?? ''}-${l.name ?? ''}-${l.createdTime ?? ''}`} />
                  : <p className="ld-empty">No rows to show.</p>)}
                {tab === 'ignored' && (preview.duplicates.length
                  ? <DbTable<DuplicateRow> variant="grid" label="Rows that will be ignored" rows={preview.duplicates} columns={duplicateColumns} rowKey={(r) => String(r.row)} />
                  : <p className="ld-empty">Nothing will be ignored. Every row in the file is new.</p>)}
                {tab === 'invalid' && <DbTable<InvalidRow> variant="grid" label="Invalid rows" rows={preview.invalid} columns={invalidColumns} rowKey={(r) => String(r.row)} />}
              </div>
              {tab === 'ignored' && preview.duplicatesTruncated && <p className="ld-map-foot">Showing the first {preview.duplicates.length}. The full list is in the report after the import.</p>}
              {tab === 'invalid' && preview.invalidTruncated && <p className="ld-map-foot">Showing the first {preview.invalid.length}. The full list is in the report after the import.</p>}
            </section>

          </div>
        )}

        {stage === 'importing' && <div className="ld-busy is-inline" role="status"><Loader2 className="ld-spin" size={18} aria-hidden="true" /><span>Importing {counts?.new.toLocaleString()} leads. Please keep this window open.</span></div>}

        {stage === 'result' && result && resultCounts && (
          <div className="ld-result" role="status">
            <span className="ld-result-icon"><CheckCircle2 size={34} aria-hidden="true" /></span>
            <h3>{resultCounts.new > 0 ? `${resultCounts.new.toLocaleString()} new ${resultCounts.new === 1 ? 'lead' : 'leads'} added` : 'No new leads were added'}</h3>
            <p>{resultSummary(resultCounts)}.{result.assignedCount ? ` ${result.assignedCount.toLocaleString()} assigned.` : ''}</p>
            <p className="ld-map-foot">{allNewLine(resultCounts) ?? 'The report lists every row of the file with what happened to it.'}</p>
            <div className="ld-counts is-three">
              <div className="ld-count is-ok"><span>Added</span><strong className="tnum">{resultCounts.new.toLocaleString()}</strong><small>new leads</small></div>
              <div className={cn('ld-count', resultCounts.duplicate && 'is-warn')}><span>Duplicates ignored</span><strong className="tnum">{resultCounts.duplicate.toLocaleString()}</strong><small>already stored</small></div>
              <div className={cn('ld-count', resultCounts.invalid && 'is-bad')}><span>Invalid</span><strong className="tnum">{resultCounts.invalid.toLocaleString()}</strong><small>skipped</small></div>
            </div>
            {resultCounts.existingCandidate > 0 && <p className="ld-map-foot">{resultCounts.existingCandidate} of the new leads already match a candidate in the Data Bank. Open the lead to link it.</p>}
          </div>
        )}
      </div>
    </Dialog>
  )
}
