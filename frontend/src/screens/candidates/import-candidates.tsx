import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, CircleAlert, Download, FileSpreadsheet, FolderOpen, Info, LoaderCircle, Plane, RotateCcw, Upload } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { ApiError } from '@/shared/api/client'
import { cn } from '@/shared/lib/cn'
import { Badge, PageHeader, Panel, PanelHeader, type Tone } from '@/shared/ui/display'
import { Button, Field, Select } from '@/shared/ui/primitives'
import {
  fileTooLarge, groupPicked, importApi,
  type CheckResponse, type ImportPlan, type ImportResult, type PickedFolders, type SheetRows,
} from '@/features/candidate-import/import-api'

type Progress = { status: 'waiting' | 'running' | 'done' | 'failed'; result?: ImportResult; error?: string }

const FALLBACK_KINDS = [{ key: 'other', label: 'Other documents' }]
const PARALLEL = 2

const ACTION_LABEL: Record<ImportPlan['action'], string> = {
  deploy: 'Deployed',
  in_progress: 'Continues on the stage screen',
  documents_only: 'Documents only',
  skip: 'Will not be imported',
}
const ACTION_TONE: Record<ImportPlan['action'], Tone> = { deploy: 'green', in_progress: 'navy', documents_only: 'neutral', skip: 'stamp' }

function placementText(plan: ImportPlan): string {
  if (plan.action === 'deploy') return 'All 11 stages recorded, deployed'
  if (plan.action === 'in_progress') {
    return plan.completedThrough > 0
      ? `Stages 1-${plan.completedThrough} recorded, continue at stage ${plan.currentStage}`
      : 'Starts at stage 1 (prescreening)'
  }
  if (plan.action === 'documents_only') return 'Candidate and documents, no job attached'
  return 'Fix the problem below, or leave it out'
}

function resultText(result: ImportResult): string {
  if (result.action === 'deploy') return 'Imported: all 11 stages recorded, deployed'
  if (result.action === 'in_progress') {
    return (result.completedThrough ?? 0) > 0
      ? `Imported: stages 1-${result.completedThrough} recorded, continue at stage ${result.currentStage}`
      : 'Imported: starts at stage 1 (prescreening)'
  }
  return 'Imported: candidate and documents'
}

function StageStrip({ plan }: { plan: ImportPlan }) {
  const done = plan.action === 'deploy' ? 11 : plan.action === 'in_progress' ? plan.completedThrough : 0
  return (
    <div className="flex items-center gap-[3px]" aria-label={placementText(plan)} role="img">
      {Array.from({ length: 11 }, (_, i) => i + 1).map((n) => (
        <span
          key={n}
          title={`Stage ${n}`}
          className={cn(
            'h-2 w-3 rounded-[2px]',
            n <= done ? 'bg-[color:var(--success-ink,#15803d)]' : plan.action === 'in_progress' && n === plan.currentStage ? 'bg-[color:var(--primary)]' : 'bg-[color:var(--tint-3)]',
          )}
        />
      ))}
    </div>
  )
}

function IssueList({ issues }: { issues: ImportPlan['issues'] }) {
  if (!issues.length) return null
  return (
    <ul className="mt-2 space-y-1 text-[13px]">
      {issues.map((issue, i) => (
        <li key={i} className={cn('flex items-start gap-2', issue.level === 'error' ? 'text-[color:var(--error-ink)]' : issue.level === 'warning' ? 'text-[color:var(--warn-ink,#b45309)]' : 'text-ink-500')}>
          {issue.level === 'error' ? <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" /> : issue.level === 'warning' ? <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> : <Info className="mt-0.5 h-4 w-4 shrink-0" />}
          <span>{issue.message}</span>
        </li>
      ))}
    </ul>
  )
}

export default function ImportCandidatesPage() {
  const { me, users, orders, orderLabel, refresh, notify } = useStore()
  const isStaff = me.role === 'staff'
  const folderInput = useRef<HTMLInputElement>(null)
  const sheetInput = useRef<HTMLInputElement>(null)

  const [picked, setPicked] = useState<PickedFolders | null>(null)
  const [sheet, setSheet] = useState<{ name: string; data: SheetRows } | null>(null)
  const [sheetError, setSheetError] = useState('')
  const [order, setOrder] = useState('')
  const [recruiter, setRecruiter] = useState(isStaff ? me.id : '')
  const [withoutJob, setWithoutJob] = useState(false)
  /** A job chosen for one candidate (order reference by the candidate's key in the list). */
  const [jobs, setJobs] = useState<Record<string, string>>({})
  const [rereading, setRereading] = useState(false)
  const [kinds, setKinds] = useState<Record<string, Record<string, string>>>({})
  const [kindChoices, setKindChoices] = useState(FALLBACK_KINDS)
  const [check, setCheck] = useState<CheckResponse | null>(null)
  const [checking, setChecking] = useState(false)
  const [checkError, setCheckError] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const [leave, setLeave] = useState<Set<string>>(new Set())
  const [progress, setProgress] = useState<Record<string, Progress>>({})
  const [running, setRunning] = useState(false)
  const latest = useRef(0)

  useEffect(() => {
    void importApi.kinds().then((r) => setKindChoices(r.kinds)).catch(() => {})
  }, [])

  const openOrders = useMemo(() => orders.filter((o) => o.status === 'open'), [orders])
  const recruiters = useMemo(() => users.filter((u) => u.active && (u.role === 'staff' || u.role === 'manager')), [users])

  const readSheetFile = useCallback(async (file: File) => {
    setSheetError('')
    try {
      const data = await importApi.readSheet(file)
      setSheet({ name: file.name, data })
    } catch (error) {
      setSheet(null)
      setSheetError(error instanceof ApiError ? error.message : 'The sheet could not be read.')
    }
  }, [])

  const choose = (list: FileList | null) => {
    if (!list || list.length === 0) return
    const grouped = groupPicked(list)
    setPicked(grouped)
    setKinds({}); setJobs({}); setProgress({}); setCheck(null); setLeave(new Set()); setOpen(null)
    if (grouped.sheet) void readSheetFile(grouped.sheet)
  }

  // The dry run: every change to the folder, the sheet, the defaults or a document's type shows the outcome again.
  useEffect(() => {
    if (!picked || picked.folders.length === 0) return
    const ticket = ++latest.current
    setChecking(true)
    const timer = window.setTimeout(() => {
      importApi.check({
        folders: picked.folders.map((f) => ({ folder: f.folder, files: f.files.map(({ path }) => ({ path, ...(kinds[f.folder]?.[path] ? { kind: kinds[f.folder][path] } : {}) })) })),
        rows: sheet?.data.rows ?? [],
        defaultOrder: openOrders.find((o) => o.id === order)?.ref ?? '',
        defaultRecruiterId: recruiter,
        jobs,
      }).then((r) => { if (ticket === latest.current) { setCheck(r); setCheckError('') } })
        .catch((e) => { if (ticket === latest.current) setCheckError(e instanceof ApiError ? e.message : 'The check failed. Try again.') })
        .finally(() => { if (ticket === latest.current) setChecking(false) })
    }, 350)
    return () => window.clearTimeout(timer)
  }, [picked, sheet, order, recruiter, kinds, jobs, openOrders])

  const plans = check?.plans ?? []
  const byFolder = useMemo(() => new Map((picked?.folders ?? []).map((f) => [f.folder, f])), [picked])
  const pickedFor = (plan: ImportPlan) => byFolder.get(plan.parent ?? plan.folder)
  const parentOf = (plan: ImportPlan) => plan.parent ?? plan.folder
  const importable = plans.filter((p) => p.action !== 'skip' && !leave.has(p.folder) && progress[p.folder]?.status !== 'done')
  /** Some candidate has no job yet (none on the screen, in the sheet or chosen for them): their stages would stay locked. */
  const needsJob = importable.some((p) => !p.order)
  const counts = useMemo(() => ({
    deploy: plans.filter((p) => p.action === 'deploy').length,
    in_progress: plans.filter((p) => p.action === 'in_progress').length,
    documents_only: plans.filter((p) => p.action === 'documents_only').length,
    skip: plans.filter((p) => p.action === 'skip').length,
  }), [plans])
  const doneCount = Object.values(progress).filter((p) => p.status === 'done').length
  const failedCount = Object.values(progress).filter((p) => p.status === 'failed').length

  const importedIds = Object.values(progress).flatMap((p) => (p.status === 'done' && p.result?.candidateId ? [p.result.candidateId] : []))
  const readAgain = async () => {
    setRereading(true)
    try {
      const { queued } = await importApi.readAgain(importedIds)
      notify(queued > 0 ? `${queued} document${queued === 1 ? '' : 's'} put back in the queue to be read again.` : 'Nothing needed reading again.')
    } catch (error) {
      notify(error instanceof ApiError ? error.message : 'Could not queue the readings. Try again.', 'warn')
    } finally {
      setRereading(false)
    }
  }

  const run = async (targets: ImportPlan[]) => {
    if (running || targets.length === 0) return
    setRunning(true)
    setProgress((prev) => ({ ...prev, ...Object.fromEntries(targets.map((p) => [p.folder, { status: 'waiting' } as Progress])) }))
    const queue = [...targets]
    const defaults = { order: openOrders.find((o) => o.id === order)?.ref ?? '', recruiterId: recruiter }
    const worker = async () => {
      for (let plan = queue.shift(); plan; plan = queue.shift()) {
        const folder = pickedFor(plan)
        if (!folder) continue
        setProgress((prev) => ({ ...prev, [plan.folder]: { status: 'running' } }))
        try {
          const result = await importApi.importOne(folder, plan, kinds[parentOf(plan)] ?? {}, defaults)
          setProgress((prev) => ({ ...prev, [plan.folder]: result.action === 'skip' ? { status: 'failed', result, error: result.issues[0]?.message ?? 'Not imported.' } : { status: 'done', result } }))
        } catch (error) {
          setProgress((prev) => ({ ...prev, [plan.folder]: { status: 'failed', error: error instanceof ApiError ? error.message : 'Could not reach the server. Retry this candidate.' } }))
        }
      }
    }
    await Promise.all(Array.from({ length: Math.min(PARALLEL, targets.length) }, worker))
    setRunning(false)
    await refresh({ keepCurrent: true }).catch(() => {})
    notify('Import finished. Check the results below.')
    // A re-check shows imported people as "already in the CRM", which is true; leave the results as the record of this run.
  }

  const sheetNeeds = plans.filter((p) => !p.hasRow).length
  const stepTwoReady = Boolean(picked && picked.folders.length > 0)

  return (
    <>
      <PageHeader
        title="Import candidates"
        description="Pick the folder that holds one sub-folder per candidate. Their documents are sorted, the profile is read from the resume and passport, and the stages are recorded as far as the documents show. Whatever is missing, you continue from there."
      />

      <Panel flush className="mb-4">
        <div className="grid gap-4 p-5 md:grid-cols-[1fr_1fr_auto] md:items-end">
          <Field label="Job order" hint="The job for everyone below. Open a candidate to give them a different job.">
            <Select value={order} onChange={(e) => setOrder(e.target.value)} disabled={running}>
              <option value="">Choose the job order</option>
              {openOrders.map((o) => <option key={o.id} value={o.id}>{o.ref} · {orderLabel(o.id)}</option>)}
            </Select>
          </Field>
          {isStaff ? (
            <Field label="Recruiter" hint="Candidates you import are assigned to you.">
              <Select value={me.id} disabled><option value={me.id}>{me.name}</option></Select>
            </Field>
          ) : (
            <Field label="Recruiter" hint="Who owns these candidates.">
              <Select value={recruiter} onChange={(e) => setRecruiter(e.target.value)} disabled={running}>
                <option value="">Me ({me.name})</option>
                {recruiters.filter((u) => u.id !== me.id).map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
              </Select>
            </Field>
          )}
          <div className="flex items-center gap-3">
            <input
              ref={folderInput}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => { choose(e.target.files); e.target.value = '' }}
              {...({ webkitdirectory: '', directory: '' } as object)}
            />
            <Button variant="primary" icon={<FolderOpen />} onClick={() => folderInput.current?.click()} disabled={running}>
              {picked ? 'Choose another folder' : 'Choose folder'}
            </Button>
          </div>
        </div>
        {picked && needsJob && !order && (
          <div role="status" className="mx-5 mb-4 rounded-[8px] border border-[color:var(--warn-edge,#f59e0b)] bg-[color:var(--warn-fill,#fffbeb)] px-4 py-3 text-[13px] text-ink">
            <p className="font-medium">Choose the job order first.</p>
            <p className="mt-1 text-ink-500">Without a job, the candidates are saved with their documents only and every stage stays locked until they are attached to a job.</p>
            <label className="mt-2 flex items-center gap-2 text-ink-500">
              <input type="checkbox" checked={withoutJob} onChange={(e) => setWithoutJob(e.target.checked)} disabled={running} />
              Import the documents only, without a job
            </label>
          </div>
        )}
        {picked && (
          <p className="px-5 pb-4 text-sm text-ink-500">
            {picked.folders.length} candidate folder{picked.folders.length === 1 ? '' : 's'},{' '}
            {picked.folders.reduce((sum, f) => sum + f.files.length, 0)} files
            {picked.ignored > 0 && ` (${picked.ignored} other files left out)`}
            {picked.folders.length === 0 && " - no candidate folders were found. Pick the folder that contains the candidates' folders."}
          </p>
        )}
        {stepTwoReady && (
          <details className="border-t border-rule-soft px-5 py-3">
            <summary className="cursor-pointer text-sm font-medium text-ink">Have phone numbers, dates or advance amounts? Add them with a sheet (optional)</summary>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Button icon={<Download />} onClick={() => void importApi.downloadSheet(picked!.folders.map((f) => f.folder)).catch(() => notify('The sheet could not be made. Try again.', 'warn'))}>
                Download the sheet
              </Button>
              <input ref={sheetInput} type="file" accept=".xlsx,.csv" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) void readSheetFile(f); e.target.value = '' }} />
              <Button icon={<FileSpreadsheet />} onClick={() => sheetInput.current?.click()} disabled={running}>
                {sheet ? 'Replace the filled sheet' : 'Upload the filled sheet'}
              </Button>
            </div>
            <p className="mt-2 text-[13px] text-ink-500">
              {sheet
                ? `${sheet.name}: ${sheet.data.rows.length} rows read.${sheet.data.ignored.length ? ` Columns not used: ${sheet.data.ignored.join(', ')}.` : ''}`
                : 'Not needed. Without it, phone numbers and details come from the resume and passport, and the interview date and advance amount are left blank.'}
            </p>
            {sheetError && <p role="alert" className="mt-1 text-[13px] text-[color:var(--error-ink)]">{sheetError}</p>}
          </details>
        )}
      </Panel>

      {stepTwoReady && (
        <Panel flush>
          <PanelHeader
            title="Candidates"
            meta={checking ? 'Checking…' : check ? `${counts.deploy} deployed · ${counts.in_progress} continue on the stage screen · ${counts.documents_only} documents only · ${counts.skip} not imported` : undefined}
            action={(
              <>
                {importedIds.length > 0 && !running && (
                  <Button icon={rereading ? <LoaderCircle className="animate-spin" /> : <RotateCcw />} onClick={() => void readAgain()} disabled={rereading}>
                    Read the documents again
                  </Button>
                )}
                {failedCount > 0 && !running && (
                  <Button icon={<RotateCcw />} onClick={() => void run(plans.filter((p) => progress[p.folder]?.status === 'failed'))}>Retry {failedCount} failed</Button>
                )}
                <Button variant="primary" icon={running ? <LoaderCircle className="animate-spin" /> : <Upload />} disabled={running || checking || importable.length === 0 || (needsJob && !order && !withoutJob)}
                  onClick={() => void run(importable)}>
                  {running ? `Importing… ${doneCount} of ${Object.keys(progress).length}` : `Import ${importable.length} candidate${importable.length === 1 ? '' : 's'}`}
                </Button>
              </>
            )}
          />
          {checkError && <p role="alert" className="px-5 pt-4 text-sm text-[color:var(--error-ink)]">{checkError}</p>}
          {check && sheetNeeds > 0 && sheet && (
            <p className="px-5 pt-4 text-[13px] text-ink-500">{sheetNeeds} folder{sheetNeeds === 1 ? ' has' : 's have'} no row in the sheet (rows are matched on the Folder or Name column).</p>
          )}
          {check && check.unmatchedRows.length > 0 && (
            <p className="px-5 pt-4 text-[13px] text-[color:var(--warn-ink,#b45309)]">
              Sheet rows with no matching folder: {check.unmatchedRows.map((r) => r.name || r.folder || `row ${r.rowNumber}`).join(', ')}.
            </p>
          )}
          <ul className="divide-y divide-rule-soft">
            {plans.map((plan) => {
              const state = progress[plan.folder]
              const expanded = open === plan.folder
              const chosen = kinds[parentOf(plan)] ?? {}
              return (
                <li key={plan.folder} className="px-5 py-3">
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                    <button type="button" className="flex min-w-0 flex-1 items-center gap-2 text-left" onClick={() => setOpen(expanded ? null : plan.folder)} aria-expanded={expanded}>
                      {expanded ? <ChevronDown className="h-4 w-4 shrink-0" /> : <ChevronRight className="h-4 w-4 shrink-0" />}
                      <span className="min-w-0">
                        <span className="block truncate font-medium text-ink">{plan.name}{plan.parent && plan.parent !== plan.folder && <span className="ml-2 text-[12px] font-normal text-ink-500">from folder {plan.parent}</span>}</span>
                        <span className="block truncate text-[13px] text-ink-500">
                          {[plan.order, plan.recruiter].filter(Boolean).join(' · ')}{plan.order || plan.recruiter ? ' · ' : ''}{plan.files.filter((f) => !f.skipped).length} files
                        </span>
                      </span>
                    </button>
                    <StageStrip plan={plan} />
                    <span className="hidden w-[16rem] text-[13px] text-ink-500 lg:block">{state?.status === 'done' && state.result ? resultText(state.result) : placementText(plan)}</span>
                    {state?.status === 'done' ? (
                      <span className="flex items-center gap-2 text-[13px] text-[color:var(--success-ink,#15803d)]">
                        <CheckCircle2 className="h-4 w-4" />
                        <Link to={`/candidates/${state.result?.candidateId}`} className="underline">{state.result?.ref}</Link>
                        {state.result?.action === 'deploy' && <Plane className="h-4 w-4" aria-label="Deployed" />}
                      </span>
                    ) : state?.status === 'running' || state?.status === 'waiting' ? (
                      <span className="flex items-center gap-2 text-[13px] text-ink-500"><LoaderCircle className="h-4 w-4 animate-spin" />{state.status === 'running' ? 'Importing' : 'Waiting'}</span>
                    ) : state?.status === 'failed' ? (
                      <span className="flex items-center gap-2 text-[13px] text-[color:var(--error-ink)]"><CircleAlert className="h-4 w-4" />Failed</span>
                    ) : (
                      <>
                        <Badge tone={ACTION_TONE[plan.action]}>{ACTION_LABEL[plan.action]}</Badge>
                        {plan.action !== 'skip' && (
                          <label className="flex items-center gap-1.5 text-[13px] text-ink-500">
                            <input type="checkbox" checked={!leave.has(plan.folder)} disabled={running}
                              onChange={(e) => setLeave((prev) => { const next = new Set(prev); if (e.target.checked) next.delete(plan.folder); else next.add(plan.folder); return next })} />
                            Import
                          </label>
                        )}
                      </>
                    )}
                  </div>
                  {state?.status === 'failed' && <p role="alert" className="mt-1 text-[13px] text-[color:var(--error-ink)]">{state.error}</p>}
                  {state?.status === 'done' && state.result && <IssueList issues={state.result.issues.filter((i) => i.level !== 'info' || state.result?.action !== 'deploy')} />}
                  {(expanded || (plan.action === 'skip' && !state)) && (
                    <div className="mt-2 pl-6">
                      {expanded && !state && (
                        <div className="mb-3 max-w-sm">
                          <Field label="Job for this candidate">
                            <Select compact value={jobs[plan.folder] ?? ''} disabled={running}
                              onChange={(e) => setJobs((prev) => ({ ...prev, [plan.folder]: e.target.value }))}>
                              <option value="">{order ? 'Same as above' : plan.order ? `From the sheet (${plan.order})` : 'No job'}</option>
                              {openOrders.map((o) => <option key={o.id} value={o.ref}>{o.ref} · {orderLabel(o.id)}</option>)}
                            </Select>
                          </Field>
                        </div>
                      )}
                      <IssueList issues={expanded ? plan.issues : plan.issues.filter((i) => i.level === 'error')} />
                      {expanded && (
                        <table className="mt-3 w-full max-w-3xl text-[13px]">
                          <thead><tr className="text-left text-ink-500"><th className="py-1 pr-3 font-medium">File</th><th className="py-1 font-medium">Treated as</th></tr></thead>
                          <tbody>
                            {plan.files.map((file) => (
                              <tr key={file.path} className="border-t border-rule-soft">
                                <td className="max-w-[22rem] truncate py-1.5 pr-3" title={file.path}>{file.path}</td>
                                <td className="py-1.5">
                                  {file.skipped || fileTooLarge(pickedFor(plan)?.files.find((f) => f.path === file.path)?.file ?? new File([], 'x'))
                                    ? <span className="text-ink-500">{file.problem ?? 'Over 25 MB, left out'}</span>
                                    : (
                                      <Select compact value={chosen[file.path] ?? ''} disabled={running || Boolean(state)}
                                        onChange={(e) => setKinds((prev) => ({ ...prev, [parentOf(plan)]: { ...(prev[parentOf(plan)] ?? {}), [file.path]: e.target.value } }))}>
                                        <option value="">{file.guessed ? 'Not recognised by name: will be read' : file.label ?? 'Other documents'}</option>
                                        {kindChoices.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
                                      </Select>
                                    )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </div>
                  )}
                </li>
              )
            })}
            {check && plans.length === 0 && <li className="px-5 py-6 text-sm text-ink-500">Nothing to import.</li>}
          </ul>
        </Panel>
      )}
    </>
  )
}
