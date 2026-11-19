import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, LoaderCircle, SearchX } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { ApiError } from '@/shared/api/client'
import { PageHeader } from '@/shared/ui/display'
import { primaryId } from '@/features/candidates/candidate-ids'
import {
  groupPicked, hashPicked, importApi, listCounts, newImportKey, openReadings, peopleFor, readAgainMessage,
  type CheckResponse, type DuplicateChoice, type IdentifyResult, type ImportedStatus, type ImportPlan, type ImportResult, type Placement, type PickedFolders, type SheetRows,
} from '@/features/candidate-import/import-api'
import {
  barModel, batchReading, candidateLabel, cardStatus, computeSteps, filterCounts, assignAllTo, filterItems, personReadingReason, nextPoll, readBar, readName, readingSummary, rootNameOf, mergeStatuses, showToolbar,
  isImportable, plansSkippedAsDuplicates, progressFor, runCounts, trackedIds,
  type ListFilter, type ReadReason, type RowProgress,
} from '@/features/candidate-import/import-view'
import { ImportStepper } from '@/features/candidate-import/components/import-stepper'
import { FolderPicker } from '@/features/candidate-import/components/folder-picker'
import { DefaultsRow } from '@/features/candidate-import/components/defaults-row'
import { CheckingSkeleton, ErrorCard } from '@/features/candidate-import/components/states'
import { ReadActionCard, ReadProgressCard, ReadQuietLink, ReadSummaryBar, UnplacedTray } from '@/features/candidate-import/components/read-cards'
import { ListToolbar } from '@/features/candidate-import/components/list-toolbar'
import { CandidateCard, type CardActions, type OpenData } from '@/features/candidate-import/components/candidate-card'
import { BatchProgress } from '@/features/candidate-import/components/result-track'
import { ActionBar } from '@/features/candidate-import/components/action-bar'
import '@/styles/import-candidates.css'
import { isAdminLevel } from '@/shared/model/role-labels'

type Progress = RowProgress<ImportResult>

const FALLBACK_KINDS = [{ key: 'other', label: 'Other documents' }]
const PARALLEL = 2
const NO_FILES: { path: string; file: File }[] = []
const NO_CHOICES: Record<string, string> = {}

export default function ImportCandidatesPage() {
  const { me, users, orders, orderLabel, refresh, notify, candidates } = useStore()
  /** Only an admin or MD chooses the recruiter; a staff member imports for themselves (the server enforces it). */
  const importsForSelf = !isAdminLevel(me.role)

  const [picked, setPicked] = useState<PickedFolders | null>(null)
  const [rootName, setRootName] = useState('')
  const [sheet, setSheet] = useState<{ name: string; data: SheetRows } | null>(null)
  const [sheetError, setSheetError] = useState('')
  const [order, setOrder] = useState('')
  const [recruiter, setRecruiter] = useState(importsForSelf ? me.id : '')
  /** A job chosen for one candidate (order reference by the candidate's key in the list). */
  const [jobs, setJobs] = useState<Record<string, string>>({})
  const [rereading, setRereading] = useState(false)
  const [kinds, setKinds] = useState<Record<string, Record<string, string>>>({})
  /** How folders become candidates (see the check API): all folders, and a choice for one picked folder. */
  const [groupMode] = useState<'auto' | 'one'>('auto')
  const [grouping, setGrouping] = useState<Record<string, 'auto' | 'one'>>({})
  /** The person's own choice per file: moved to another candidate of the same folder, or left out (""). */
  const [assign, setAssign] = useState<Record<string, string>>({})
  const [kindChoices, setKindChoices] = useState(FALLBACK_KINDS)
  const [check, setCheck] = useState<CheckResponse | null>(null)
  const [checking, setChecking] = useState(false)
  const [checkError, setCheckError] = useState('')
  const [checkTick, setCheckTick] = useState(0)
  const [open, setOpen] = useState<string | null>(null)
  const [leave, setLeave] = useState<Set<string>>(new Set())
  const [progress, setProgress] = useState<Record<string, Progress>>({})
  const [running, setRunning] = useState(false)
  const [filter, setFilter] = useState<ListFilter>('all')
  const [query, setQuery] = useState('')
  const latest = useRef(0)
  /** Reading the files to find each candidate (see the check API's `people`): the result per picked folder, and what a person decided for the rows nobody owned. */
  const [identified, setIdentified] = useState<Record<string, IdentifyResult>>({})
  const [placement, setPlacement] = useState<Record<string, Placement>>({})
  const [reading, setReading] = useState<string | null>(null)
  const [readError, setReadError] = useState<{ parent: string; message: string } | null>(null)
  const readAbort = useRef<AbortController | null>(null)
  const lookSeen = useRef(new Set<string>())
  /** What a person chose for a candidate the check found in the CRM, by card (Skip when nothing is chosen). */
  const [planChoices, setPlanChoices] = useState<Record<string, DuplicateChoice>>({})
  /** One key per import attempt of a card (made once, kept when the same attempt is asked again, new ones only when another folder is picked). */
  const importKeys = useRef<Record<string, string>>({})
  /** The duplicate choice a card was last imported with, so a retry asks for the same thing. */
  const sentChoice = useRef<Record<string, DuplicateChoice>>({})
  /** Reading each file to tell the server which ones it already holds: how far that is (null when not running). */
  const [hashing, setHashing] = useState<{ done: number; total: number } | null>(null)

  useEffect(() => {
    void importApi.kinds().then((r) => setKindChoices(r.kinds)).catch(() => {})
  }, [])

  const openOrders = useMemo(() => orders.filter((o) => o.status === 'open'), [orders])
  const recruiters = useMemo(() => users.filter((u) => u.active && (u.role === 'staff' || u.role === 'manager')).map((u) => ({ id: u.id, name: u.name })), [users])
  const jobChoices = useMemo(() => openOrders.map((o) => ({ ref: o.ref, label: `${o.ref} · ${orderLabel(o.id)}` })), [openOrders, orderLabel])
  const orderChoices = useMemo(() => openOrders.map((o) => ({ id: o.id, label: `${o.ref} · ${orderLabel(o.id)}` })), [openOrders, orderLabel])

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

  const choose = useCallback((list: FileList | File[] | null, root?: string) => {
    if (!list || list.length === 0) return
    const grouped = groupPicked(list)
    setPicked(grouped)
    setRootName(root ?? rootNameOf(list))
    readAbort.current?.abort()
    setKinds({}); setJobs({}); setGrouping({}); setAssign({}); setProgress({}); setCheck(null); setLeave(new Set()); setOpen(null)
    setIdentified({}); setPlacement({}); setReading(null); setReadError(null); lookSeen.current = new Set()
    setPlanChoices({}); importKeys.current = {}; sentChoice.current = {}; setHashing(null)
    setFilter('all'); setQuery(''); setCheckError('')
    if (grouped.sheet) void readSheetFile(grouped.sheet)
    else { setSheet(null); setSheetError('') }
  }, [readSheetFile])

  const people = useMemo(
    () => Object.fromEntries(Object.entries(identified).map(([folder, result]) => [folder, peopleFor(result, placement[folder] ?? {})])),
    [identified, placement],
  )

  // The dry run: every change to the folder, the sheet, the defaults or a document's type shows the outcome again.
  useEffect(() => {
    if (!picked || picked.folders.length === 0) return
    const ticket = ++latest.current
    setChecking(true)
    const timer = window.setTimeout(() => {
      void (async () => {
        // Every file's SHA-256 goes with the check so the server finds a file it already holds whatever it is called. Made once per file; if it cannot be made, the check goes without it.
        let sums: Record<string, Record<string, string>> = {}
        try {
          sums = await hashPicked(picked.folders, { onProgress: (done, total) => { if (ticket === latest.current) setHashing(done < total ? { done, total } : null) }, stop: () => ticket !== latest.current })
        } catch { sums = {} }
        if (ticket !== latest.current) return
        setHashing(null)
        await importApi.check({
          folders: picked.folders.map((f) => ({
            folder: f.folder,
            files: f.files.map(({ path }) => ({ path, ...(kinds[f.folder]?.[path] ? { kind: kinds[f.folder][path] } : {}), ...(sums[f.folder]?.[path] ? { sha256: sums[f.folder][path] } : {}) })),
          })),
          rows: sheet?.data.rows ?? [],
          defaultOrder: openOrders.find((o) => o.id === order)?.ref ?? '',
          defaultRecruiterId: recruiter,
          jobs, groupMode, grouping, assign, people,
        }).then((r) => {
          if (ticket !== latest.current) return
          setCheck(r); setCheckError('')
          // A person whose documents disagree is not ticked for import until someone ticks them (once; their later choice stays).
          const fresh = r.plans.filter((p) => p.needsLook && !lookSeen.current.has(p.folder))
          if (fresh.length) {
            fresh.forEach((p) => lookSeen.current.add(p.folder))
            setLeave((prev) => new Set([...prev, ...fresh.map((p) => p.folder)]))
          }
        })
          .catch((e) => { if (ticket === latest.current) setCheckError(e instanceof ApiError ? e.message : 'The check failed. Try again.') })
          .finally(() => { if (ticket === latest.current) setChecking(false) })
      })()
    }, 350)
    return () => window.clearTimeout(timer)
  }, [picked, sheet, order, recruiter, kinds, jobs, groupMode, grouping, assign, people, openOrders, checkTick])

  const plans = useMemo(() => check?.plans ?? [], [check])
  const byFolder = useMemo(() => new Map((picked?.folders ?? []).map((f) => [f.folder, f])), [picked])
  const parentOf = (plan: ImportPlan) => plan.parent ?? plan.folder
  /** The candidates of each picked folder, in list order. */
  const parents = useMemo(() => {
    const map = new Map<string, ImportPlan[]>()
    for (const plan of plans) {
      const key = plan.parent ?? plan.folder
      const list = map.get(key)
      if (list) list.push(plan); else map.set(key, [plan])
    }
    return map
  }, [plans])
  /** Some candidate of this picked folder was already imported (or is on its way): regrouping the folder would import the same people twice. */
  const touchedParents = useMemo(() => new Set(plans.filter((p) => progress[p.folder]).map((p) => p.parent ?? p.folder)), [plans, progress])

  /** The line under the folder name: counted from the candidates the list shows (after any reading of the files), never from the picked folders. */
  const headerText = (() => {
    if (!picked) return ''
    const folders = picked.folders.length
    const files = (count: number) => `${count} file${count === 1 ? '' : 's'}`
    if (!check) return `${folders} folder${folders === 1 ? '' : 's'} · ${files(picked.folders.reduce((sum, f) => sum + f.files.length, 0))}`
    const { candidates: count, files: fileCount, unassigned } = listCounts(check.plans)
    return `${count} candidate${count === 1 ? '' : 's'}${folders > 1 && folders !== count ? ` from ${folders} folders` : ''} · ${files(fileCount)}${unassigned > 0 ? `, ${unassigned} more not given to anyone yet` : ''}`
  })()

  /** For each picked folder: do the file names already say whose the files are? Only when they do not is reading the files offered up front. */
  const readInfo = useMemo(() => {
    const info = new Map<string, { needs: boolean; reason: ReadReason | null; mostlyUnnamed: boolean; fileCount: number }>()
    for (const [parent, mine] of parents) {
      const leftOut = (mine[0]?.leftOut ?? []).length
      const all = new Set(mine.flatMap((p) => p.files.filter((f) => !f.skipped).map((f) => f.path))).size + leftOut
      const reason = personReadingReason(mine, { pickedFolders: picked?.folders.length ?? 1 })
      info.set(parent, {
        needs: reason !== null,
        reason,
        // The names split the folder, but most of its files say nobody's name: that split is not the answer.
        mostlyUnnamed: !mine[0]?.identified && mine.length > 1 && leftOut * 2 > all,
        fileCount: byFolder.get(parent)?.files.length ?? 0,
      })
    }
    return info
  }, [parents, picked, byFolder])

  /** Change how one folder becomes candidates; the choices made for its files under the old grouping no longer apply. */
  const regroup = useCallback((parent: string, mode: 'auto' | 'one') => {
    setGrouping((prev) => ({ ...prev, [parent]: mode }))
    setAssign((prev) => Object.fromEntries(Object.entries(prev).filter(([key]) => !key.startsWith(`${parent}/`))))
    // Only this folder's candidates change: the other folders keep the "Import" ticks the person already took off.
    setLeave((prev) => new Set([...prev].filter((key) => key !== parent && !key.startsWith(`${parent} / `))))
    setOpen(null)
  }, [])
  /** Read this picked folder's files (locally, no online reader) and make one candidate per person found in them. */
  const readFolder = async (parent: string) => {
    const folder = byFolder.get(parent)
    if (!folder || reading) return
    const controller = new AbortController()
    readAbort.current = controller
    setReading(parent); setReadError(null)
    try {
      const result = await importApi.identify(folder, controller.signal)
      setIdentified((prev) => ({ ...prev, [parent]: result }))
      setPlacement((prev) => ({ ...prev, [parent]: {} }))
      setAssign((prev) => Object.fromEntries(Object.entries(prev).filter(([key]) => !key.startsWith(`${parent}/`))))
      setLeave((prev) => new Set([...prev].filter((key) => key !== parent && !key.startsWith(`${parent} / `))))
      setGrouping((prev) => { const next = { ...prev }; delete next[parent]; return next })
      setOpen(null)
    } catch (error) {
      if (!controller.signal.aborted) setReadError({ parent, message: error instanceof ApiError ? error.message : 'The files could not be read. Try again.' })
    } finally {
      setReading(null)
      readAbort.current = null
    }
  }
  const forgetReading = (parent: string) => {
    setIdentified((prev) => { const next = { ...prev }; delete next[parent]; return next })
    setPlacement((prev) => { const next = { ...prev }; delete next[parent]; return next })
    setLeave((prev) => new Set([...prev].filter((key) => key !== parent && !key.startsWith(`${parent} / `))))
    setOpen(null)
  }

  const importable = useMemo(
    () => plans.filter((p) => isImportable(p, { left: leave.has(p.folder), progress: progress[p.folder], choice: planChoices[p.folder] })),
    [plans, leave, progress, planChoices],
  )
  // Duplicates are counted apart: an answer of "duplicate", a skip or documents added to an existing candidate is never an import.
  const counts0 = useMemo(() => runCounts(Object.values(progress)), [progress])
  const doneCount = counts0.imported
  const failedCount = counts0.failed
  const startedCount = counts0.started
  const duplicatesLeft = useMemo(() => plansSkippedAsDuplicates(plans, planChoices, progress), [plans, planChoices, progress])

  /** The candidates asked about while their documents are read: the imported ones and those that got a resume, passport, Aadhaar or education file added. Never a skipped duplicate. */
  const importedIds = useMemo(() => trackedIds(Object.values(progress)), [progress])
  /** What each imported candidate looks like now, kept up to date while their documents are being read. */
  const [live, setLive] = useState<Record<string, ImportedStatus>>({})
  const importedKey = importedIds.join(',')
  const [readRound, setReadRound] = useState(0)   // bumped when the person starts the reading, so the summary follows it
  const [updatedAt, setUpdatedAt] = useState<number | null>(null)
  const rereadingRef = useRef(false)
  rereadingRef.current = rereading
  useEffect(() => {
    if (importedIds.length === 0) return
    let stopped = false
    let timer = 0
    let round = 0
    let waiting: string[] = importedIds        // the first call asks about everyone; after that only about the candidates whose reading is still open
    let inFlight = false
    const schedule = () => {
      window.clearTimeout(timer)
      const delay = nextPoll({ open: waiting.length, requesting: rereadingRef.current, hidden: document.hidden, round })
      if (!stopped && delay !== null) timer = window.setTimeout(() => load(waiting.length > 0 ? waiting : importedIds), delay)
    }
    const load = (ids: string[]) => {
      if (inFlight) return
      inFlight = true
      void importApi.status(ids).then(({ items }) => {
        if (stopped) return
        // Candidates whose status did not change keep their object, so their cards are not drawn again on every poll.
        setLive((prev) => mergeStatuses(prev, items))
        setUpdatedAt(Date.now())
        setRereading(false)         // the first answer after "Read the documents" shows what was queued
        round += 1
        waiting = openReadings(items)
      }).catch(() => { if (!stopped) { setRereading(false); round += 1 } })
        .finally(() => { inFlight = false; if (!stopped) schedule() })
    }
    // A hidden tab stops asking; coming back asks at once.
    const back = () => { if (!document.hidden && !stopped && (waiting.length > 0 || rereadingRef.current)) { window.clearTimeout(timer); load(waiting.length > 0 ? waiting : importedIds) } }
    document.addEventListener('visibilitychange', back)
    window.addEventListener('focus', back)
    load(importedIds)
    return () => { stopped = true; window.clearTimeout(timer); document.removeEventListener('visibilitychange', back); window.removeEventListener('focus', back) }
  }, [importedKey, readRound]) // eslint-disable-line react-hooks/exhaustive-deps
  const readAgain = async () => {
    setRereading(true)       // the bar switches to "Reading the documents…" at once and stays so until the status shows what was queued
    try {
      const { queued, readable } = await importApi.readAgain(importedIds)
      const message = readAgainMessage(queued, readable ?? 0)
      notify(message.text, message.tone)
      if (queued > 0) setReadRound((n) => n + 1)
      else setRereading(false)
    } catch (error) {
      notify(error instanceof ApiError ? error.message : 'Could not queue the readings. Try again.', 'warn')
      setRereading(false)
    }
  }

  /** "Import more": back to choosing a folder; what was imported stays in the CRM. */
  const importMore = () => {
    readAbort.current?.abort()
    setPicked(null); setRootName(''); setSheet(null); setSheetError(''); setKinds({}); setJobs({}); setGrouping({}); setAssign({}); setProgress({})
    setPlanChoices({}); importKeys.current = {}; sentChoice.current = {}; setHashing(null)
    setCheck(null); setLeave(new Set()); setOpen(null); setIdentified({}); setPlacement({}); setReading(null); setReadError(null); lookSeen.current = new Set()
    setFilter('all'); setQuery(''); setCheckError(''); setLive({}); setUpdatedAt(null); setRereading(false)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const run = async (targets: ImportPlan[], choose: Record<string, DuplicateChoice> = {}) => {
    if (running || targets.length === 0) return
    setRunning(true)
    setProgress((prev) => ({ ...prev, ...Object.fromEntries(targets.map((p) => [p.folder, { status: 'waiting' } as Progress])) }))
    const queue = [...targets]
    const defaults = { order: openOrders.find((o) => o.id === order)?.ref ?? '', recruiterId: recruiter }
    const worker = async () => {
      for (let plan = queue.shift(); plan; plan = queue.shift()) {
        const folder = byFolder.get(parentOf(plan))
        if (!folder) continue
        setProgress((prev) => ({ ...prev, [plan.folder]: { status: 'running' } }))
        try {
          const duplicateAction = choose[plan.folder] ?? sentChoice.current[plan.folder] ?? (plan.alreadyInCrm ? planChoices[plan.folder] : undefined)
          if (duplicateAction) sentChoice.current[plan.folder] = duplicateAction
          // The same key for every try of this attempt: a repeated request is answered "already imported" and never makes a second candidate.
          const importKey = (importKeys.current[plan.folder] ??= newImportKey())
          const result = await importApi.importOne(folder, plan, kinds[parentOf(plan)] ?? {}, defaults, readName(plan, identified[parentOf(plan)]?.people.find((p) => p.name === plan.name)), { importKey, duplicateAction })
          setProgress((prev) => ({ ...prev, [plan.folder]: progressFor(result) }))
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
  // The card actions never change identity: they read the newest `run` and `readFolder` through this ref.
  const latestFns = useRef({ run, readFolder })
  latestFns.current = { run, readFolder }

  const actions = useMemo<CardActions>(() => ({
    toggleOpen: (folder) => setOpen((prev) => (prev === folder ? null : folder)),
    tick: (folder, checked) => setLeave((prev) => { const next = new Set(prev); if (checked) next.delete(folder); else next.add(folder); return next }),
    setKind: (parent, path, kind) => setKinds((prev) => ({ ...prev, [parent]: { ...(prev[parent] ?? {}), [path]: kind } })),
    setAssign: (parent, path, target) => setAssign((prev) => ({ ...prev, [`${parent}/${path}`]: target === '__out__' ? '' : target })),
    setJob: (folder, ref) => setJobs((prev) => ({ ...prev, [folder]: ref })),
    regroup,
    putBack: (key) => setAssign((prev) => { const next = { ...prev }; delete next[key]; return next }),
    assignLeftOut: (parent, path, target) => setAssign((prev) => ({ ...prev, [`${parent}/${path}`]: target })),
    // The same per-file "Belongs to" choice, made for every unplaced file at once.
    assignAllLeftOut: (parent, paths, target) => setAssign((prev) => ({ ...prev, ...assignAllTo(parent, paths, target) })),
    readFiles: (parent) => { void latestFns.current.readFolder(parent) },
    retry: (plan) => { void latestFns.current.run([plan]) },
    planChoice: (folder, choice) => {
      setPlanChoices((prev) => ({ ...prev, [folder]: choice }))
      // Choosing to add the documents (or import anyway) puts the card in the import; Skip takes it out again.
      if (choice !== 'skip') setLeave((prev) => { const next = new Set(prev); next.delete(folder); return next })
    },
    answerDuplicate: (plan, choice) => {
      if (choice === 'skip' || choice === 'reopen') {
        setProgress((prev) => (prev[plan.folder] ? { ...prev, [plan.folder]: { ...prev[plan.folder], status: choice === 'skip' ? 'skipped' : 'duplicate' } } : prev))
        return
      }
      void latestFns.current.run([plan], { [plan.folder]: choice })
    },
  }), [regroup])

  // --------------------------------------------------------------------------- what the list shows
  const items = useMemo(() => plans.map((plan) => ({ plan, status: cardStatus(plan, progress[plan.folder]) })), [plans, progress])
  const counts = useMemo(() => filterCounts(items.map((item) => item.status)), [items])
  const toolbar = showToolbar(plans.length)
  const visible = useMemo(() => (toolbar ? filterItems(items, filter, query) : items), [items, filter, query, toolbar])

  const existingByRef = useMemo(() => {
    if (!plans.some((p) => p.duplicateOf)) return new Map<string, string>()
    return new Map(candidates.map((c) => [c.ref, c.id]))
  }, [plans, candidates])

  const batch = useMemo(() => batchReading(importedIds.flatMap((id) => (live[id] ? [live[id]] : []))), [importedIds, live])
  const summary = useMemo(() => readingSummary(importedIds.flatMap((id) => (live[id] ? [live[id]] : [])), importedIds.length, rereading), [importedIds, live, rereading])
  const readFinished = importedIds.length > 0 && (summary.phase === 'read' || summary.phase === 'review')
  const readNow = readBar(summary, importedIds.length)

  const steps = computeSteps({ hasFolder: Boolean(picked && picked.folders.length > 0), importStarted: startedCount > 0, importing: running, importedCount: counts0.settled - counts0.failed, failedCount, readFinished })
  const unticked = plans.filter((p) => p.needsLook && p.action !== 'skip' && leave.has(p.folder) && !progress[p.folder]).length
  const documents = useMemo(() => listCounts(importable).files, [importable])
  const bar = barModel({
    checking: checking || !check, reading: reading !== null, importing: running, importable: importable.length, documents,
    imported: doneCount, failed: failedCount, started: startedCount, unticked, hasList: plans.length > 0,
    duplicatesSkipped: counts0.skipped, duplicatesWaiting: counts0.waiting, duplicatesLeft, added: counts0.added,
    readState: readFinished ? 'done' : summary.phase === 'reading' || summary.phase === 'starting' ? 'reading' : 'none',
  })
  const failedPlans = plans.filter((p) => progress[p.folder]?.status === 'failed')

  /** What a card is called in a choice: the name read from the documents, else the folder or file group (never presented as the name). */
  const labelOf = (plan: ImportPlan) => candidateLabel(plan, plan.identified ? identified[parentOf(plan)]?.people.find((p) => p.name === plan.name) : undefined)
  const openData = (plan: ImportPlan): OpenData => {
    const parent = parentOf(plan)
    const mine = parents.get(parent) ?? []
    return {
      chosen: kinds[parent] ?? NO_CHOICES,
      label: labelOf(plan),
      siblings: mine.filter((o) => o.folder !== plan.folder).map((o) => ({ folder: o.folder, name: labelOf(o), locked: Boolean(progress[o.folder]) })),
      pickedFiles: byFolder.get(parent)?.files ?? NO_FILES,
      kindChoices,
      jobChoices,
      jobDefault: order ? 'Same as above' : plan.order ? `From the sheet (${plan.order})` : 'No job',
      jobValue: jobs[plan.folder] ?? '',
      oneCandidate: (grouping[parent] ?? groupMode) === 'one',
      putBack: Object.keys(assign).filter((key) => assign[key] === '' && key.startsWith(`${parent}/`)).map((key) => ({ key, path: key.slice(parent.length + 1) })),
    }
  }

  const sheetNeeds = plans.filter((p) => !p.hasRow).length
  const hasFolder = Boolean(picked && picked.folders.length > 0)
  const manyFolders = (picked?.folders.length ?? 0) > 1
  const nameOf = (parent: string) => (manyFolders ? parent : undefined)
  const quietParent = parents.size === 1 ? [...parents.keys()][0] : null

  return (
    <div className="ic-page">
      <PageHeader title="Import candidates" />
      <ImportStepper steps={steps} />

      <FolderPicker
        onFiles={choose} rootName={rootName} summary={headerText} hasFolder={hasFolder}
        noCandidates={Boolean(picked && picked.folders.length === 0)} busy={running}
      />
      <DefaultsRow
        order={order} onOrder={setOrder} recruiter={recruiter} onRecruiter={setRecruiter} jobs={orderChoices} recruiters={recruiters}
        me={me} importsForSelf={importsForSelf} disabled={running}
      />

      {hasFolder && (
        <>
          {sheetError && <p className="ic-note is-warn" role="alert"><AlertTriangle aria-hidden="true" />{sheetError}</p>}
          {check && sheetNeeds > 0 && sheet && (
            <p className="ic-note">{sheetNeeds} folder{sheetNeeds === 1 ? ' has' : 's have'} no row in the sheet (rows are matched on the Folder or Name column).</p>
          )}
          {check && check.unmatchedRows.length > 0 && (
            <p className="ic-note is-warn"><AlertTriangle aria-hidden="true" />Sheet rows with no matching folder: {check.unmatchedRows.map((r) => r.name || r.folder || `row ${r.rowNumber}`).join(', ')}.</p>
          )}

          {checkError && <ErrorCard title="The folder could not be checked" onRetry={() => { setCheckError(''); setCheckTick((n) => n + 1) }}>{checkError}</ErrorCard>}
          {hashing && (
            <p className="ic-note" role="status" aria-live="polite"><LoaderCircle className="animate-spin" aria-hidden="true" />Checking files… {hashing.done} of {hashing.total}</p>
          )}
          {!check && !checkError && <CheckingSkeleton rows={Math.min(4, Math.max(2, picked?.folders.length ?? 2))} />}

          {check && [...parents.keys()].map((parent) => {
            if (touchedParents.has(parent)) return null
            const info = readInfo.get(parent)
            const result = identified[parent]
            const failure = readError && readError.parent === parent && reading === null ? readError.message : ''
            if (result) {
              return (
                <div key={parent} className="ic-read-group">
                  <ReadSummaryBar folderName={nameOf(parent)} result={result} placement={placement[parent] ?? NO_CHOICES} running={running}
                    onForget={() => forgetReading(parent)} />
                  <UnplacedTray result={result} placement={placement[parent] ?? NO_CHOICES} running={running}
                    onPlace={(id, target) => setPlacement((prev) => {
                      const mine = { ...(prev[parent] ?? {}) }
                      if (target === undefined) delete mine[id]; else mine[id] = target
                      return { ...prev, [parent]: mine }
                    })} />
                </div>
              )
            }
            if (reading === parent) return <ReadProgressCard key={parent} folderName={nameOf(parent)} fileCount={info?.fileCount ?? 0} onCancel={() => readAbort.current?.abort()} />
            if (info?.needs && reading === null) {
              return <ReadActionCard key={parent} folderName={nameOf(parent)} fileCount={info.fileCount} mostlyUnnamed={info.mostlyUnnamed} reason={info.reason} error={failure} disabled={running} onRead={() => void readFolder(parent)} />
            }
            if (failure) return <ErrorCard key={parent} title="The files could not be read" onRetry={() => void readFolder(parent)}>{failure}</ErrorCard>
            return null
          })}
          {check && quietParent && !identified[quietParent] && reading === null && !readInfo.get(quietParent)?.needs && !touchedParents.has(quietParent) && plans.length > 0 && (
            <ReadQuietLink disabled={running} onRead={() => void readFolder(quietParent)} />
          )}

          {importedIds.length > 0 && (doneCount > 0 || batch.total > 0) && <BatchProgress batch={batch} imported={doneCount} failed={failedCount} />}

          {check && (
            <section className="ic-list" aria-label="Candidates">
              <header className="ic-list-head">
                <h2>Candidates</h2>
                <span className="ic-list-sub">{checking ? 'Checking…' : `${plans.length} ${plans.length === 1 ? 'candidate' : 'candidates'}`}</span>
              </header>
              {toolbar && <ListToolbar filter={filter} counts={counts} onFilter={setFilter} query={query} onQuery={setQuery} />}
              {plans.length === 0 ? (
                <p className="ic-empty">Nothing to import. Every file in this folder was left out.</p>
              ) : visible.length === 0 ? (
                <p className="ic-empty"><SearchX aria-hidden="true" />No candidates match. <button type="button" className="ic-link" onClick={() => { setFilter('all'); setQuery('') }}>Show everyone</button></p>
              ) : (
                <ul className="ic-cards">
                  {visible.map(({ plan }) => {
                    const parent = parentOf(plan)
                    const expanded = open === plan.folder
                    const state = progress[plan.folder]
                    const id = state?.result?.candidateId
                    const dupId = plan.duplicateOf ? existingByRef.get(plan.duplicateOf) ?? null : null
                    return (
                      <CandidateCard
                        key={plan.folder}
                        plan={plan}
                        actions={actions}
                        state={state}
                        status={id ? live[id] : undefined}
                        checked={!leave.has(plan.folder)}
                        expanded={expanded}
                        busy={running}
                        touched={touchedParents.has(parent)}
                        readResult={identified[parent]}
                        soleCandidate={(parents.get(parent)?.length ?? 0) === 1}
                        existing={plan.duplicateOf ? { id: dupId, label: primaryId({ ref: plan.duplicateOf, careId: plan.duplicateOfCareId }) } : undefined}
                        choice={planChoices[plan.folder]}
                        canReadFiles={!identified[parent] && reading === null && !readInfo.get(parent)?.needs}
                        open={expanded ? openData(plan) : undefined}
                      />
                    )
                  })}
                </ul>
              )}
            </section>
          )}
        </>
      )}

      {hasFolder && (
        <ActionBar
          model={bar} failed={failedCount} anyImported={importedIds.length > 0} importing={running} rereading={rereading}
          read={readNow} updatedAt={updatedAt} onMore={importMore}
          fraction={startedCount > 0 ? counts0.settled / startedCount : 0}
          onImport={() => void run(importable)} onRetry={() => void run(failedPlans)} onRead={() => void readAgain()}
        />
      )}
    </div>
  )
}
