import { lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactElement, type ReactNode } from 'react'
import { choosesRecruiter } from '@/features/candidates/candidate-access'
import { followUpHint, NOT_ASSIGNED_YET, RECRUITER_FIELD_HINT, recruiterChoices, saveButtonLabel, savebarStatus, savedNotice, savedOwnerNote } from '@/features/candidates/assignment-labels'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, ClipboardList, BriefcaseBusiness, Camera, Check, CheckCircle2, CalendarClock, Gauge, PhoneCall, FolderOpen, GraduationCap, IdCard, LoaderCircle, RotateCw, Share2, Target, UserRound, Contact, Trash2 } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { api, ApiError } from '@/shared/api/client'
import { PageHeader } from '@/shared/ui/display'
import { Button, Input, Select, Textarea } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { documentFileError } from '@/shared/lib/document-files'
import { documentLabel } from '@/shared/model/document-slots'
import { dateTimeLabel } from '@/shared/lib/format'
import { DatePicker } from '@/shared/ui/date-picker'
import { mergeOcrForm, ocrSavePayload, type DocumentScans } from '@/features/candidates/ocr-form'
import { normalizedSkills } from '@/features/candidates/candidate-skills'
import { passportExpiryNotice } from '@/features/candidates/passport-expiry'
import { IDLE_POLL_MS, scansFromJobs, useDocumentIntake, useQueueClock, type IntakeJob } from '@/features/candidates/use-document-intake'
import { documentPresence, fileRows, foundChips, isDocumentJob, isWorking, MAX_DOCUMENT_FILES, readingStatus, readProgress, severalPeopleFound } from '@/features/candidates/entry-upload'
import { jobReports } from '@/features/candidates/read-report'
import { EntryUploadZone } from '@/features/candidates/components/entry-upload-zone'
import { DocumentBundleReview } from '@/features/candidates/components/document-bundle-review'
import { EntrySection, TagField } from '@/features/candidates/components/entry-section'
import { EntryField, FormSection, InterestCards, PhoneField, ReadBadge, SameAs } from '@/features/candidates/components/entry-fields'
import { EntryRowsEditor } from '@/features/candidates/components/entry-rows-editor'
import { EntryProgress } from '@/features/candidates/components/entry-progress'
import { entryCompleteness, nextDocument, screeningMissing } from '@/features/candidates/entry-completeness'
import { effectiveNativePlace, effectiveWhatsapp, screeningPayload, whatsappIsSame } from '@/features/candidates/entry-extras'
import { useFormOptions, useFormSuggestions } from '@/features/form-options/use-form-options'
import { activeOptions, choicesFor, FORM_OPTION_CATEGORIES as OPT } from '@/features/form-options/form-options-model'
import { frequentValues } from '@/shared/lib/suggestions'
import { EntryAvatar } from '@/features/candidates/components/entry-avatar'
import { DuplicateDialog } from '@/features/candidates/components/entry-dialogs'
import { duplicateFromSaveError, finishSavedCandidate, freshWorkspace } from '@/features/candidates/save-flow'
import { primaryId } from '@/features/candidates/candidate-ids'
import { fieldSources, liveDuplicateQuery, SCAN_KIND_LABEL, type DuplicateMatch } from '@/features/candidates/entry-signals'
import { candidateEntryErrors, draftSnapshot, EMPTY_CANDIDATE_FORM as EMPTY } from '@/features/candidates/entry-model'
import { educationPayload, historyPayload, previewFormFromEntry } from '@/features/application-form/entry-bridge'
import { applicationFormHref } from '@/features/application-form/form-model'
import { FormOptionSelect } from '@/features/application-form/components/form-option-select'
import { fileHref } from '@/shared/api/client'
import { useReferenceAgencies } from '@/features/clients/agency-api'
import '@/styles/application-form.css'
import '@/styles/add-candidate-v2.css'
import '@/styles/application-entry.css'

// The printable form brings its own sections and layout. Download it when requested,
// so opening Candidate never waits for a preview that is still closed.
const EntryFormPreview = lazy(() => import('@/features/application-form/components/entry-form-preview')
  .then((module) => ({ default: module.EntryFormPreview })))

type FormKey = keyof typeof EMPTY
type SavedResult = { ref?: string; name?: string; ownerName: string | null; merged: boolean; at: number }
type LiveCheck = { status: 'idle' | 'checking' | 'none' | 'found' | 'conflict' | 'unavailable'; match?: DuplicateMatch; message?: string }
const SLA_MS = 48 * 3600 * 1000
const VALIDATION_MESSAGE = 'Please fix the fields marked in red, then save again. Nothing you typed was lost.'

export default function AddCandidatePage() {
  const store = useStore()
  // Only an admin or MD chooses the recruiter; a staff member who adds a candidate keeps it.
  const picksRecruiter = choosesRecruiter(store.me.role)
  const navigate = useNavigate()
  const intake = useDocumentIntake(store.me!.id)
  // The dropdown and card lists come from the database (Agency settings > Form options); the hook falls back to a built-in list only if the request fails.
  const { options: formOptions } = useFormOptions()
  const served = useFormSuggestions()
  // The blank form: empty, except for the nationality the agency chose to pre-fill (none unless an admin sets one).
  const blank = useMemo(() => ({ ...EMPTY, nationality: store.settings.defaultNationality ?? '' }), [store.settings.defaultNationality])
  const [form, setForm] = useState(blank)
  const [saving, setSaving] = useState(false)
  const [bundleSource, setBundleSource] = useState<string | null>(null)
  const [savedCandidateId, setSavedCandidateId] = useState<string | null>(null)
  const [savedResult, setSavedResult] = useState<SavedResult | null>(null)
  const [uploadErrors, setUploadErrors] = useState<Record<string, string>>({})
  const [saveError, setSaveError] = useState('')
  const [previewing, setPreviewing] = useState(false)
  const [attached, setAttached] = useState(false)

  const [duplicate, setDuplicate] = useState<{ candidate: DuplicateMatch; matchedOn: string[] } | null>(null)
  const [live, setLive] = useState<LiveCheck>({ status: 'idle' })
  const liveTicket = useRef(0)
  const [errors, setErrors] = useState<Record<string, string>>({})

  // Inspection can succeed without extraction, so it has its own visible result.
  const [scans, setScans] = useState<DocumentScans>({})
  const scansRef = useRef<DocumentScans>({})
  const edited = useRef(new Set<FormKey>())
  const readController = useRef<AbortController | null>(null)
  const [addProblems, setAddProblems] = useState<string[]>([])
  const [splitSuggested, setSplitSuggested] = useState(false)
  const [readMessage, setReadMessage] = useState('')
  /** Why a reading round could not finish (plain words); shown with a Try again button until the next round starts. */
  const [readError, setReadError] = useState('')
  const [reading, setReading] = useState(false)
  // The newest readDocuments: files are read from inside async handlers whose own copy of the state may be a render old.
  const readDocumentsRef = useRef<(options?: { retryFailed?: boolean; jobIds?: string[] }) => Promise<void>>(async () => {})

  useEffect(() => () => {
    readController.current?.abort()
    readController.current = null
  }, [])

  const set = (k: FormKey, v: string) => {
    // A ref also catches changes made after a scan starts but before it returns.
    edited.current.add(k)
    setForm((f) => ({ ...f, [k]: v }))
    setErrors((e) => ({ ...e, [k]: '' }))
    setSaveError('')
  }

  // Only the keys the draft endpoint knows are sent (the screening details included, under the server names).
  const snapshotDraft = (candidateId = savedCandidateId) => ({ ...draftSnapshot(form, edited.current), savedCandidateId: candidateId })
  const draftRef = useRef(snapshotDraft())
  draftRef.current = snapshotDraft()
  // Typing plans ONE autosave request at most every 2.5 s (never two at once, nothing sent when nothing changed). Leaving a field,
  // hiding the tab or closing the page sends what is waiting at once (features/candidates/draft-saver.ts).
  useEffect(() => {
    if (!intake.jobs.length) return
    intake.queueDraft(draftRef.current)
  }, [form, intake.jobs.length, savedCandidateId])
  useEffect(() => {
    const flush = () => intake.flushDraft()
    const hidden = () => { if (document.visibilityState === 'hidden') flush() }
    // Focus moving from one field to the next keeps the 2.5 s pacing; focus leaving the fields altogether (a button, another area,
    // the window) sends what is waiting.
    const left = (event: FocusEvent) => {
      const next = event.relatedTarget as HTMLElement | null
      if (!next || !/^(INPUT|SELECT|TEXTAREA)$/.test(next.tagName)) flush()
    }
    document.addEventListener('visibilitychange', hidden)
    document.addEventListener('focusout', left)
    window.addEventListener('pagehide', flush)
    return () => { document.removeEventListener('visibilitychange', hidden); document.removeEventListener('focusout', left); window.removeEventListener('pagehide', flush) }
  }, [])

  // Live duplicate check: the same rule the save uses (phone, email, passport number), run once the typing settles.
  const liveKey = JSON.stringify(liveDuplicateQuery(form))
  useEffect(() => {
    const query = liveDuplicateQuery(form)
    const ticket = ++liveTicket.current
    if (!query || savedCandidateId) { setLive({ status: 'idle' }); return }
    setLive((previous) => previous.status === 'found' ? previous : { status: 'checking' })
    const timer = setTimeout(async () => {
      try {
        const result = await api.post<{ duplicate: boolean; candidate?: DuplicateMatch }>('/candidates/check-duplicate', query)
        if (ticket !== liveTicket.current || !store.isCurrentSession()) return
        setLive(result.duplicate && result.candidate ? { status: 'found', match: result.candidate } : { status: 'none' })
      } catch (error) {
        if (ticket !== liveTicket.current || !store.isCurrentSession()) return
        setLive(error instanceof ApiError && error.status === 409 ? { status: 'conflict', message: error.message } : { status: 'unavailable' })
      }
    }, 700)
    return () => clearTimeout(timer)
  }, [liveKey, savedCandidateId])

  /** Puts what the server has read so far into the form: unedited fields follow the documents, typed values are never replaced. */
  const applyJobs = (jobs: IntakeJob[]) => {
    if (!store.isCurrentSession()) return
    const read = scansFromJobs(jobs)
    scansRef.current = read; setScans(read)
    setForm((previous) => mergeOcrForm(previous, blank, read, edited.current))
    setErrors((previous) => Object.fromEntries(Object.entries(previous).filter(([key]) => edited.current.has(key as FormKey))))
    // The reminder about highlighted fields is stale once documents have filled them in.
    setSaveError((message) => message === VALIDATION_MESSAGE ? '' : message)
  }

  /**
   * Any file may hold a resume, a passport and an Aadhaar card. Every file is sent as kind "resume" (the server classifies the pages of
   * every file and reads each kind it finds) and is added next to the others. Originals are durable before reading; values you typed
   * are never replaced (see mergeOcrForm).
   */
  const addDocuments = async (files: File[]) => {
    if (saving || savedCandidateId || files.length === 0) return
    const problems: string[] = []
    const good: File[] = []
    for (const file of files) {
      const problem = documentFileError(file)
      if (problem) problems.push(`${file.name}: ${problem}`)
      else good.push(file)
    }
    const room = Math.max(0, MAX_DOCUMENT_FILES - intake.jobs.filter(isDocumentJob).length)
    if (good.length > room) {
      problems.push(`You can add up to ${MAX_DOCUMENT_FILES} files here. ${good.length - room} ${good.length - room === 1 ? 'file was' : 'files were'} not added.`)
      good.length = room
    }
    setAddProblems(problems)
    if (!good.length) return
    setReadMessage('')
    for (const file of good) {
      try {
        await intake.upload('resume', file, { many: true })
        if (!store.isCurrentSession()) return
      } catch (error) {
        problems.push(`${file.name}: ${error instanceof Error ? error.message : 'We could not save this file. Please try again.'}`)
        setAddProblems([...problems])
      }
    }
    if (!store.isCurrentSession()) return
    await intake.saveDraft(draftRef.current).catch(() => {})
    // No Extract step: the moment the files are saved the reader starts on them (a file the server already had is not read twice).
    if (intake.hasUnqueued()) void readDocumentsRef.current({ retryFailed: false })
  }

  const removeDocument = async (id: string) => {
    if (saving || savedCandidateId) return
    try {
      const rest = await intake.remove(id)
      if (!store.isCurrentSession()) return
      setAddProblems([]); applyJobs(rest)
      await intake.saveDraft(draftRef.current).catch(() => {})
    } catch (error) { setAddProblems([error instanceof Error ? error.message : 'We could not remove the file. Please try again.']) }
  }

  const retryFile = async (id: string) => {
    if (saving || savedCandidateId) return
    try { await intake.retry(id); setAddProblems([]); void readDocuments({ retryFailed: false, jobIds: [id] }) }
    catch (error) { setAddProblems([error instanceof Error ? error.message : 'We could not queue this file again. Please try again.']) }
  }

  // One page the sorting could not place: the person's choice is filed by the server; a resume / passport / Aadhaar page is read again.
  const placePage = async (id: string, page: number, kind: string) => {
    if (saving) return
    try {
      const saved = await intake.placePage(id, page, kind)
      setAddProblems([])
      if (saved.status === 'pending' || saved.status === 'processing' || saved.status === 'retry') void readDocuments({ retryFailed: false, jobIds: [id] })
    } catch (error) { setAddProblems([error instanceof Error ? error.message : 'We could not place this page. Please try again.']) }
  }

  const queueClock = useQueueClock(intake.jobs)
  // "Check again" on a file that is not moving: ask the server for the reader state right now.
  const checkReader = async () => { await intake.refresh().then(applyJobs, () => {}) }

  const documentRows = useMemo(() => fileRows(intake.jobs, queueClock), [intake.jobs, queueClock])
  const chips = useMemo(() => foundChips(intake.jobs), [intake.jobs])
  const readReports = useMemo(() => jobReports(intake.jobs.filter(isDocumentJob)), [intake.jobs])
  const presence = documentPresence(intake.jobs)
  const failedFiles = documentRows.filter((row) => row.state === 'failed').length
  // Where the reading really is (sorting the pages, then reading what was found), re-worked with every poll of the server.
  const readingNote = reading ? readingStatus(intake.jobs).text : ''
  // The strip under the zone: only while something is going on or went wrong; nothing to press otherwise.
  const showFoot = reading || intake.uploading || Boolean(readError) || failedFiles > 0 || (severalPeopleFound(intake.jobs, splitSuggested) && Boolean(intake.jobs[0]?.groupId))
  const stillWorking = intake.jobs.some((job) => isDocumentJob(job) && isWorking(job))
  // After Stop waiting (or when a saved upload is opened) nothing is being waited on; a slower look keeps the rows and the form current.
  useEffect(() => {
    if (reading || !stillWorking || savedCandidateId) return
    const controller = new AbortController()
    const timer = window.setTimeout(() => { void intake.refresh(controller.signal).then(applyJobs, () => {}) }, IDLE_POLL_MS)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [reading, stillWorking, intake.jobs, savedCandidateId])
  // The server accepts a blank name or phone exactly when an upload is attached (intake_group_id is sent).
  const hasDocuments = intake.jobs.length > 0
  /**
   * Reads the files of this upload and fills the form. It starts by itself when files are saved (see addDocuments) and again for files
   * added while a round is running; `retryFailed` is only for the Try again button. A round that ends with an error is not restarted
   * by itself (that would loop): Try again does it.
   */
  const readDocuments = async (options: { retryFailed?: boolean; jobIds?: string[] } = {}) => {
    if (readController.current || saving || savedCandidateId) return
    const jobIds = options.jobIds ?? intake.latest().filter(isDocumentJob).map((job) => job.id)
    if (jobIds.length === 0) return
    const controller = new AbortController()
    readController.current = controller
    let failed = false
    setReading(true); setReadMessage(''); setReadError('')
    try {
      // A failed autosave must never stop the document from being read; the draft banner already tells the user.
      await intake.saveDraft(draftRef.current).catch(() => {})
      // Each poll puts what is read so far into the rows, the found chips and the form.
      const jobs = await intake.read(controller.signal, { ...options, jobIds, onProgress: (values) => { if (readController.current === controller) applyJobs(values) } })
      if (readController.current !== controller || !store.isCurrentSession()) return
      applyJobs(jobs)
    } catch (error) {
      failed = true
      if (readController.current === controller && store.isCurrentSession()) setReadError(error instanceof Error && error.name !== 'AbortError' ? error.message : 'We could not finish reading. Your files are saved.')
    } finally {
      if (readController.current === controller) {
        readController.current = null; setReading(false)
        // Files added while this round was running are read next, without anyone pressing anything.
        if (!failed && !controller.signal.aborted && intake.hasUnqueued()) void readDocumentsRef.current({ retryFailed: false })
      }
    }
  }
  readDocumentsRef.current = readDocuments

  const cancelReading = () => {
    readController.current?.abort()
    readController.current = null
    setReading(false)
    setForm((prev) => mergeOcrForm(prev, blank, scansRef.current, edited.current))
    setReadMessage('You stopped waiting. We keep reading in the background. Come back later, or type the details yourself.')
  }


  const save = async () => {
    if (saving || reading || intake.uploading) return
    const next = savedCandidateId ? {} : candidateEntryErrors(form, undefined, hasDocuments)
    setErrors(next)
    if (Object.keys(next).length) {
      setSaveError(VALIDATION_MESSAGE)
      // The form is long; bring the first problem into view.
      requestAnimationFrame(() => document.querySelector('.ac-f[data-invalid]')?.scrollIntoView({ block: 'center', behavior: 'smooth' }))
      return
    }

    setSaving(true)
    setSaveError('')
    try {
      if (savedCandidateId) await uploadDocuments(savedCandidateId)
      else await doSave()
    } catch (error) {
      if (error instanceof ApiError && error.status === 409 && typeof error.detail === 'object' && error.detail && (error.detail as { splitSuggested?: boolean }).splitSuggested) setSplitSuggested(true)
      // The server refuses a duplicate before it writes anything and names the match: the decision dialog opens, with no error banner.
      const match = duplicateFromSaveError<DuplicateMatch>(error)
      if (match) setDuplicate({ candidate: match, matchedOn: match.matchedOn })
      else if (store.isCurrentSession()) setSaveError(error instanceof Error ? error.message : 'We could not save this candidate. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  // No separate duplicate check in front of the save: creating the candidate applies the same rule on the server (409 with the
  // match, nothing written), which saves a whole request. The live check beside the form still warns while typing.
  const doSave = async () => { await commit() }

  /** From the live match card: the same decision dialog, but only once the required details are valid (a combine sends the whole form). */
  const reviewMatch = () => {
    if (live.status !== 'found' || !live.match) return
    const next = candidateEntryErrors(form, undefined, hasDocuments)
    setErrors(next)
    if (Object.keys(next).length) { setSaveError(VALIDATION_MESSAGE); return }
    setDuplicate({ candidate: live.match, matchedOn: live.match.matchedOn })
  }

  const payload = () => ({
      name: form.name.trim(),
      phone: form.phone.trim(),
      whatsapp: effectiveWhatsapp(form) || undefined,
      email: form.email.trim() || undefined,
      dob: form.dob || undefined,
      gender: form.gender || undefined,
      nationality: form.nationality,
      current_city: form.currentCity,
      languages: form.languages.split(',').map((s) => s.trim()).filter(Boolean),
      passport_no: form.passportNo.trim() || undefined,
      passport_issue: form.passportIssue || undefined,
      passport_expiry: form.passportExpiry || undefined,
      passport_place: form.passportPlace || undefined,
      designations: normalizedSkills(form.designation.split(',')),
      total_experience: Number(form.totalExperience) || 0,
      gulf_experience: Number(form.gulfExperience) || 0,
      skills: normalizedSkills(form.skills.split(/[,;\n]/)),
      expected_salary: form.expectedSalary.trim() ? Number(form.expectedSalary) : undefined,
      // Application form (docx) details.
      father_name: form.fatherName.trim() || undefined,
      marital_status: form.maritalStatus || undefined,
      current_address: form.currentAddress.trim() || undefined,
      experience_india: Number(form.experienceIndia) || 0,
      education: educationPayload(form.education),
      work_history: historyPayload(form.workHistory),
      ...screeningPayload(form),
      source: form.source || 'other',   // "Not recorded": the column cannot be empty, so the catch-all source is stored
      source_detail: form.sourceDetail.trim() || undefined,
      // The server prefers the scanned resume's own text; this covers a hand-filled form.
      resume_text: `${form.name} — ${form.designation}, ${form.totalExperience || 0} years. ${form.skills}`,
      // Verbatim Veris results plus their provenance under `_sources`.
      ocr_data: ocrSavePayload(scans),
      intake_group_id: intake.jobs[0]?.groupId,
    })

  const commit = async (mergeId?: string) => {
    const created = mergeId
      ? await api.post<{ id: string; ref?: string; careId?: string | null; name?: string; ownerName?: string | null }>(`/candidates/${mergeId}/merge`, payload())
      // Only an admin or MD may name the recruiter right here; a staff member's own entry is assigned to them (the server decides).
      // The workspace refresh is started after the documents are attached (uploadDocuments), not waited for here.
      : await store.addCandidate({ ...payload(), ...(picksRecruiter && form.assignTo ? { assign_to: form.assignTo } : {}) }, { deferRefresh: true }) as { id: string; ref?: string; careId?: string | null; name?: string; ownerName?: string | null } | null
    if (!created) return
    const ownerName = created.ownerName ?? null
    if (mergeId) store.notify(`Combined with ${primaryId(created) || 'the existing candidate'}${ownerName ? `. This candidate stays with ${ownerName}` : ''}. Empty fields were filled in. No new task was created.`)
    else { const notice = savedNotice(picksRecruiter, ownerName); store.notify(notice.text, notice.tone) }
    setSavedResult({ ref: primaryId(created) || undefined, name: created.name, ownerName, merged: Boolean(mergeId), at: Date.now() })
    setSavedCandidateId(created.id)
    setDuplicate(null)
    await uploadDocuments(created.id)
  }

  /** The workspace refresh that follows the last save: the profile waits for this one instead of starting another. */
  const workspaceRefresh = useRef<Promise<boolean> | null>(null)
  const uploadDocuments = async (candidateId: string) => {
    // The draft and the attachments run together; the workspace refresh starts when they are done and is not waited for.
    const { failed, refreshed } = await finishSavedCandidate({
      saveDraft: () => intake.saveDraft(snapshotDraft(candidateId)),
      attach: () => intake.attach(candidateId, (message) => store.notify(message)),
      refresh: () => store.refresh({ keepCurrent: true }),
    })
    workspaceRefresh.current = refreshed
    void refreshed.then((worked) => { if (!worked && store.isCurrentSession()) store.notify('Candidate saved. The directory could not refresh; reopen it to try again.', 'warn') })
    if (!store.isCurrentSession()) return
    setUploadErrors(failed)
    // Stay on the saved result and offer the application form straight away (and the profile beside it).
    if (!Object.keys(failed).length) setAttached(true)
  }

  const openSavedProfile = async (candidateId: string) => {
    // The profile must contain the newly saved original files: the refresh that started after the uploads is reused when it worked.
    // A failed read leaves this recovery screen mounted with the saved record ID.
    await freshWorkspace(workspaceRefresh.current, () => store.refresh({ keepCurrent: true }))
    if (store.isCurrentSession()) navigate(`/candidates/${candidateId}`)
  }

  const combine = async (id: string) => {
    setSaving(true); setSaveError('')
    try { await commit(id) } catch (error) { setSaveError(error instanceof Error ? error.message : 'We could not combine them. Nothing you typed was lost.') } finally { setSaving(false) }
  }

  // The saved result (owner, retry buttons) sits above the form; make sure it is what the user sees.
  useEffect(() => {
    if (savedCandidateId) document.querySelector('.ac-saved')?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }, [savedCandidateId])

  // The recruiter the admin or MD picked (nobody until they do); a staff member's own candidate needs no pick.
  const chosenRecruiterName = picksRecruiter && form.assignTo ? store.user(form.assignTo)?.name ?? null : null
  const saveLabel = saveButtonLabel(picksRecruiter, chosenRecruiterName)
  const busyAny = saving || reading || intake.uploading
  const unfilled = [!form.name.trim() && 'full name', !form.phone.trim() && 'phone'].filter(Boolean) as string[]
  // With a document nothing is required: whatever it did not give stays blank and the candidate is flagged for review.
  const missing = hasDocuments ? [] : unfilled
  const sourceHint = form.source === 'referral' ? 'Referred by' : form.source === 'other' ? 'Tell us where from' : 'More about the source (optional)'
  // Reference agency names are offered as suggestions for a referral; what is typed stays free text.
  const agencyNames = useReferenceAgencies(form.source === 'referral').items?.map((agency) => agency.agencyName) ?? []
  const sourcePlaceholder = form.source === 'referral' ? 'Name of the person who referred them' : form.source === 'other' ? 'Where did this candidate come from?' : form.source === 'walk_in' ? 'Branch or event, e.g. Kochi office' : 'Optional note'
  const failedUploads = Object.keys(uploadErrors).length
  const sources = fieldSources(scans, edited.current)
  const filledCount = Object.keys(sources).length
  const expiryNotice = passportExpiryNotice(form.passportExpiry)
  const photoJob = intake.jobs.find((item) => item.kind === 'photo')
  const photoInput = useRef<HTMLInputElement>(null)
  const addPhoto = async (file: File) => {
    if (saving || reading || savedCandidateId) return
    try { await intake.upload('photo', file) } catch (error) { setAddProblems([error instanceof Error ? error.message : 'We could not save the photo. Please try again.']); return }
    await intake.saveDraft(draftRef.current).catch(() => {})
  }
  // The paper form for what has been entered so far; the same button opens the live form once the candidate is saved.
  const previewForm = previewFormFromEntry({ ...form, whatsapp: effectiveWhatsapp(form) }, {
    documents: intake.jobs.map((job) => ({ kind: job.kind, filename: job.filename, url: job.originalUrl, sizeKb: job.sizeKb })),
    sources: sources as Partial<Record<string, 'resume' | 'passport' | 'aadhaar'>>,
    edited: edited.current as ReadonlySet<string>,
    source: ({ walk_in: 'Walk-in', referral: 'Referral', email: 'Email', whatsapp: 'WhatsApp', other: 'Other' } as Record<string, string>)[form.source],
  })
  const openApplicationForm = () => { if (savedCandidateId) navigate(applicationFormHref(savedCandidateId)) }
  /** The completeness of the paper form for what is typed, read and uploaded so far. */
  const completeness = entryCompleteness(form, {
    resume: presence.resume,
    passport: presence.passport,
    photo: Boolean(photoJob),
  })
  const documentsAdded = { resume: presence.resume, passport: presence.passport, photo: Boolean(photoJob) }
  const passportAvailable = Boolean(form.passportNo.trim()) || documentsAdded.passport
  const jump = (anchor: string) => document.getElementById(anchor)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  /** "Upload passport" and the like: bring the upload zone (or the photo slot) into view and open its file picker. */
  const promptUpload = (kind: 'passport' | 'resume' | 'photo') => {
    const slot = document.getElementById(kind === 'photo' ? 'photo-box' : 'upload-zone')
    slot?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    window.setTimeout(() => slot?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.click(), 350)
  }
  const sectionMissing = (...labels: string[]) => {
    const count = completeness.missing.filter((item) => labels.includes(item.key)).length
    return count ? <span className="ac-sec-count">{count} to add</span> : <span className="ac-sec-count is-done"><Check size={12} strokeWidth={3} aria-hidden />Complete</span>
  }
  const disabledAll = saving || Boolean(savedCandidateId)
  /** One labelled field of the form, with the "Read from" badge when a document filled it and nobody typed over it. */
  const F = (key: FormKey, label: string, control: ReactElement, o: { cls?: string; required?: boolean; hint?: ReactNode; aux?: ReactNode } = {}) => (
    <EntryField label={label} className={o.cls} required={o.required} error={errors[key]} hint={o.hint} aux={o.aux} source={sources[key]}>{control}</EntryField>
  )
  const text = (key: FormKey, label: string, o: { cls?: string; required?: boolean; placeholder?: string; hint?: ReactNode; type?: string } = {}) =>
    F(key, label, <Input type={o.type} value={form[key]} onChange={(e) => set(key, e.target.value)} placeholder={o.placeholder} autoComplete="off" />, o)
  const years = (key: FormKey, label: string, cls: string) =>
    F(key, label, <Input type="number" min="0" step="1" value={form[key]} onChange={(e) => set(key, e.target.value)} placeholder="Years" />, { cls })
  const choice = (key: FormKey, label: string, category: string, cls: string, empty = 'Not recorded') => (
    F(key, label, <FormOptionSelect category={category} value={form[key]} onChange={(value) => set(key, value)} label={label} allowClear clearLabel={empty} allowAdd={category !== OPT.gender} />, { cls })
  )
  const labels = (category: string) => activeOptions(formOptions, category).map((option) => option.label)
  // Suggestions come from what is already saved (the server's lists); the page's own records stand in until they arrive.
  const countrySuggestions = useMemo(() => served.countries.length ? served.countries : frequentValues([...store.orders.map((order) => order.country), ...store.clients.map((client) => client.country), ...store.companies.map((company) => company.country)]), [served.countries, store.orders, store.clients, store.companies])
  const skillSuggestions = useMemo(() => served.skills.length ? served.skills : frequentValues(store.candidates.flatMap((candidate) => candidate.skills ?? []), 200), [served.skills, store.candidates])
  const companySuggestions = useMemo(() => served.companies.length ? served.companies : frequentValues(store.companies.map((company) => company.name)), [served.companies, store.companies])
  const designationSuggestions = useMemo(() => served.jobPreferences.length ? served.jobPreferences : store.designations.map((designation) => designation.title), [served.jobPreferences, store.designations])
  // Staff and MDs the admin or MD may hand the candidate to; nobody is chosen unless they pick.
  const recruiterOptions = useMemo(() => recruiterChoices(store.users), [store.users])
  return (
    <>
      <PageHeader
        title="Candidate"
        actions={<><Button icon={<FolderOpen />} onClick={() => navigate('/import-candidates')}>Import many candidates</Button><Button icon={<ClipboardList />} onClick={() => (savedCandidateId ? openApplicationForm() : setPreviewing(true))}>{savedCandidateId ? 'Open application form' : 'Application form'}</Button></>}
      />

      <div
        className="candidate-entry ac"
        onKeyDown={(event) => {
          if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !savedCandidateId && !event.defaultPrevented) { event.preventDefault(); void save() }
        }}
      >
        <div className="ac-stack">
          {bundleSource && <DocumentBundleReview sourceType="manual" sourceId={bundleSource} onClose={() => setBundleSource(null)} onCommitted={async result => { setSplitSuggested(false); intake.reset(); setForm(blank); edited.current = new Set(); scansRef.current = {}; setScans({}); setSavedCandidateId(null); setSavedResult(null); setErrors({}); setAddProblems([]); setUploadErrors({}); setSaveError(''); setReadMessage(`${result.candidateIds.length} candidate profiles saved from the reviewed document pages. This form is ready for a new candidate.`); await store.refresh({ keepCurrent: true }).catch(() => {}) }} />}
          {intake.draftError && <div role="alert" className="ac-inline-alert"><p>{intake.draftError}</p><Button size="sm" disabled={busyAny} onClick={() => void intake.saveDraft(snapshotDraft()).catch(() => {})}>Try saving the form again</Button></div>}
          {saveError && <p role="alert" className="ac-alert"><AlertTriangle size={16} aria-hidden /><span>{saveError}</span></p>}
          {savedCandidateId && <EntrySection icon={CheckCircle2} className="ac-saved"
            title={savedResult?.merged ? 'Combined with the existing candidate' : failedUploads ? 'Candidate saved. Some documents are not attached yet.' : 'Candidate saved'}
            meta={savedResult ? `${savedResult.name ?? 'This candidate'}${savedResult.ref ? ` · ${savedResult.ref}` : ''}` : 'Finish attaching the documents, or open the profile.'}>
            <div className="ac-body" role="alert">
              {savedResult && <div className="ac-result">
                <div className="ac-result-tile">
                  <span className="ac-result-label">{savedResult.merged ? 'Stays with' : 'Assigned to'}</span>
                  <span className="ac-result-owner">
                    {savedResult.ownerName ? <><EntryAvatar name={savedResult.ownerName} /><strong>{savedResult.ownerName}</strong></> : <strong className="ac-unassigned">Not assigned</strong>}
                  </span>
                  <small>{savedOwnerNote(picksRecruiter, savedResult.ownerName, savedResult.merged)}</small>
                </div>
                <div className="ac-result-tile">
                  <span className="ac-result-label">Initial call</span>
                  <span className="ac-result-owner"><span className="ac-result-icon"><PhoneCall size={16} aria-hidden /></span><strong>{savedResult.merged || !savedResult.ownerName ? 'No new task' : `Due by ${dateTimeLabel(new Date(savedResult.at + SLA_MS))}`}</strong></span>
                  <small>{savedResult.merged ? 'Combining only fills empty fields. It does not create a new task.' : savedResult.ownerName ? 'The recruiter has 48 hours to make the first call.' : 'The 48-hour clock starts once someone is assigned.'}</small>
                </div>
              </div>}
              <p className="ac-muted">{attached && failedUploads === 0 ? 'Everything is attached. Open the application form to check what was read, add a missing document, or go to the profile.' : 'Finish attaching the documents, or open the profile. Anything already attached stays attached.'}</p>
              {failedUploads > 0 && <ul className="ac-failed">{Object.entries(uploadErrors).map(([slot, error]) => <li key={slot}>{error}</li>)}</ul>}
              <div className="ac-actions">
                <Button variant="primary" icon={<ClipboardList />} disabled={saving} onClick={openApplicationForm}>Open application form</Button>
                {(!attached || failedUploads > 0) && <Button disabled={saving} onClick={save}>{saving ? 'Please wait…' : failedUploads ? 'Try the failed uploads again' : 'Finish attaching documents'}</Button>}
                <Button disabled={saving} onClick={async () => {
                  setSaving(true)
                  try { await openSavedProfile(savedCandidateId) }
                  catch (error) { if (store.isCurrentSession()) setSaveError(error instanceof Error ? error.message : 'We could not open the profile. Please try again.') }
                  finally { setSaving(false) }
                }}>Open saved profile</Button>
              </div>
              {failedUploads > 0 && <p className="ac-muted ac-small">Your files are still saved. Use Try the failed uploads again to attach them.</p>}
            </div>
          </EntrySection>}

          <EntryProgress completeness={completeness} reading={reading} filledByDocuments={filledCount} next={nextDocument(documentsAdded)} onJump={jump} onUpload={promptUpload} />

          <div className="ac-form-card">
            <fieldset disabled={disabledAll} className="ac-fieldset">
              {/* ------------------------------------------------------------------ Documents first */}
              <FormSection id="candidate-documents" icon={FolderOpen} title="Upload documents" subtitle="Drop all of this candidate's files together, in any order. Every page is sorted into its own field and the details fill in by themselves, then you check them and save.">
                <div className="ac-docs">
                  <EntryUploadZone rows={documentRows} chips={chips} reports={readReports} progress={readProgress(documentRows)} disabled={disabledAll} max={MAX_DOCUMENT_FILES} problems={addProblems}
                    onAdd={(files) => void addDocuments(files)} onRemove={removeDocument} onRetry={retryFile} onPlacePage={placePage}
                    onCheckAgain={checkReader} onManual={() => jump('candidate-personal')} />
                  {showFoot && (
                    <div className="ac-doc-foot">
                      <div className="ac-doc-status" aria-live="polite">
                        {(reading || intake.uploading) && <span className="ac-progress" role="progressbar" aria-label={reading ? 'Reading documents' : 'Saving your file'}><i /></span>}
                        {reading
                          ? <span><LoaderCircle size={14} className="ac-spin" aria-hidden /> Reading documents… You can leave this page; we keep going.{readingNote && <><br /><span className="ac-muted ac-small" role="status">{readingNote}</span></>}</span>
                          : intake.uploading ? <span><LoaderCircle size={14} className="ac-spin" aria-hidden /> Saving your files…</span>
                          : readError ? <span role="alert" className="ac-doc-error"><AlertTriangle size={14} aria-hidden /> {readError}</span>
                          : failedFiles > 0 ? <span role="alert" className="ac-doc-error"><AlertTriangle size={14} aria-hidden /> {failedFiles === 1 ? 'One file could not be read. It is saved. Try again, or type the details yourself.' : `${failedFiles} files could not be read. They are saved. Try again, or type the details yourself.`}</span>
                          : null}
                      </div>
                      <div className="ac-doc-buttons">
                        {severalPeopleFound(intake.jobs, splitSuggested) && intake.jobs[0]?.groupId && <Button disabled={reading || saving || intake.uploading} onClick={() => setBundleSource(intake.jobs[0].groupId)}>Check people in documents</Button>}
                        {reading && <Button variant="ghost" onClick={cancelReading}>Stop waiting</Button>}
                        {!reading && (readError || failedFiles > 0) && <Button onClick={() => void readDocuments({ retryFailed: true })} disabled={saving || intake.uploading} icon={<RotateCw className="h-4 w-4" />}>Try again</Button>}
                      </div>
                    </div>
                  )}
                  {readMessage && <p aria-live="polite" className="ac-muted ac-small ac-read-message">{readMessage}</p>}
                </div>
              </FormSection>

              {/* ------------------------------------------------------------------ Personal information */}
              <FormSection id="candidate-personal" icon={UserRound} title="Personal information" subtitle="Name and mobile number are required unless a document is attached." aside={sectionMissing('name', 'fatherName', 'dob', 'maritalStatus', 'gender', 'nationality', 'languages', 'phone', 'currentAddress')}>
                <div className="ac-personal">
                  <div id="photo-box" className="ac-photo-box">
                    <div className="ac-photo-frame">
                      {photoJob ? <img src={fileHref(photoJob.originalUrl)} alt={documentLabel('photo')} /> : <><Camera size={22} aria-hidden /><span>{documentLabel('photo')}</span></>}
                    </div>
                    <div className="ac-photo-actions">
                      <button type="button" className="ac-photo-btn" disabled={saving || intake.uploading || reading || Boolean(savedCandidateId)} onClick={() => photoInput.current?.click()}>{photoJob ? 'Replace' : 'Upload photo'}</button>
                      {photoJob && <button type="button" className="ac-photo-btn is-quiet" aria-label="Remove photo" title="Remove photo" disabled={saving || intake.uploading || reading || Boolean(savedCandidateId)}
                        onClick={async () => { await intake.remove(photoJob.id); await intake.saveDraft(draftRef.current).catch(() => {}) }}><Trash2 size={14} /></button>}
                    </div>
                    <input ref={photoInput} type="file" accept="image/jpeg,image/png,image/webp,image/heic,.jpg,.jpeg,.png,.webp,.heic" className="sr-only" tabIndex={-1} aria-label="Choose a photo"
                      onChange={(event) => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ''; if (file) void addPhoto(file) }} />
                  </div>
                  <div className="ac-g">
                    {text('name', 'Candidate name', { cls: 's-3', required: true, placeholder: 'As written in the passport' })}
                    {text('fatherName', 'Father’s name', { cls: 's-3' })}
                    {F('dob', 'Date of birth', <DatePicker value={form.dob} onChange={(value) => set('dob', value)} />, { cls: 's-3' })}
                    {choice('maritalStatus', 'Marital status', OPT.maritalStatus, 's-3')}
                    {choice('gender', 'Gender', OPT.gender, 's-3')}
                    {text('nationality', 'Nationality', { cls: 's-3' })}
                    <div className="ac-f s-6" data-from={sources.languages ? '' : undefined}>
                      <TagField label="Language known" value={form.languages} onChange={(value) => set('languages', value)} suggestions={labels(OPT.language)} placeholder="Type a language and press Enter" aux={sources.languages && <ReadBadge kind={sources.languages} />} />
                    </div>
                    {text('currentCity', 'Current location', { cls: 's-6', placeholder: 'City or town you live in now' })}
                    {F('nativePlace', 'Native place', <Input value={effectiveNativePlace(form)} disabled={form.nativeSame === 'yes'} onChange={(e) => set('nativePlace', e.target.value)} autoComplete="off" placeholder="Hometown or village" />,
                      { cls: 's-6', aux: <SameAs label="Same as current" checked={form.nativeSame === 'yes'} onChange={(checked) => set('nativeSame', checked ? 'yes' : '')} /> })}
                    {F('currentAddress', 'Current address', <Textarea rows={2} value={form.currentAddress} onChange={(e) => set('currentAddress', e.target.value)} placeholder="House / street, city, state, PIN" />, { cls: 's-12' })}
                  </div>
                </div>
                {live.status === 'found' && live.match && !savedCandidateId && (
                  <div className="ac-match" role="status">
                    <EntryAvatar name={live.match.name} size="lg" />
                    <div className="ac-match-copy">
                      <p className="ac-match-title">Looks like {live.match.name} is already in the Data Bank</p>
                      <p className="ac-match-sub"><span className="tnum">{primaryId(live.match)}</span> · Same {live.match.matchedOn.join(' and ')} · Recruiter: {live.match.ownerName ?? 'not assigned yet'}</p>
                    </div>
                    <div className="ac-match-actions">
                      <Button size="sm" onClick={() => navigate(`/candidates/${live.match!.id}`)}>Open candidate</Button>
                      <Button size="sm" variant="primary" onClick={reviewMatch} disabled={busyAny}>Review match</Button>
                    </div>
                  </div>
                )}
                {live.status === 'conflict' && !savedCandidateId && (
                  <p className="ac-match-note is-warn" role="status"><AlertTriangle size={14} aria-hidden /><span>{live.message || 'The phone, email or passport belong to different candidates. Sort this out before you save.'}</span></p>
                )}
                {live.status === 'checking' && !savedCandidateId && <p className="ac-match-note" role="status"><LoaderCircle size={14} className="ac-spin" aria-hidden /><span>Checking the Data Bank for this person…</span></p>}
              </FormSection>

              {/* ------------------------------------------------------------------ Contact details */}
              <FormSection id="candidate-contact" icon={Contact} title="Contact details" subtitle="How to reach the candidate and a family member, and when to call.">
                <div className="ac-g">
                  {F('phone', 'Mobile number', <PhoneField value={form.phone} onChange={(value) => set('phone', value)} invalid={Boolean(errors.phone)} />, { cls: 's-4', required: true })}
                  {F('whatsapp', 'WhatsApp number', <PhoneField value={effectiveWhatsapp(form)} disabled={whatsappIsSame(form)} onChange={(value) => set('whatsapp', value)} showHint={false} />,
                    { cls: 's-4', hint: whatsappIsSame(form) ? 'Messages go to the mobile number.' : undefined, aux: <SameAs label="Same as primary" checked={whatsappIsSame(form)} onChange={(checked) => set('whatsappSame', checked ? 'yes' : 'no')} /> })}
                  {F('familyNumber', 'Family member number', <PhoneField value={form.familyNumber} onChange={(value) => set('familyNumber', value)} showHint={false} />, { cls: 's-4', hint: 'A relative who can be reached if the candidate cannot.' })}
                  {text('email', 'E-mail', { cls: 's-6', type: 'email', placeholder: 'candidate@example.com' })}
                  {choice('bestTimeToContact', 'Best time to contact', OPT.bestTime, 's-6', 'No preference noted')}
                </div>
              </FormSection>

              {/* ------------------------------------------------------------------ Passport */}
              <FormSection id="candidate-passport" icon={IdCard} title="Passport details" subtitle="Read from the passport copy. The original passport is collected at stage 7."
                aside={<span className={`ac-pill ${passportAvailable ? 'is-good' : 'is-muted'}`}><i aria-hidden />{passportAvailable ? 'Passport available' : 'No passport yet'}</span>}>
                <div className="ac-g">
                  {text('passportNo', 'Passport number', { cls: 's-3', placeholder: 'M1234567' })}
                  {text('passportPlace', 'Place of issue', { cls: 's-3' })}
                  {F('passportIssue', 'Issue date', <DatePicker value={form.passportIssue} onChange={(value) => set('passportIssue', value)} />, { cls: 's-3' })}
                  {F('passportExpiry', 'Expiry date', <DatePicker value={form.passportExpiry} onChange={(value) => set('passportExpiry', value)} />, { cls: 's-3', hint: expiryNotice ? `${expiryNotice.text}. Most Gulf countries want six months left.` : undefined })}
                </div>
              </FormSection>

              {/* ------------------------------------------------------------------ Education */}
              <FormSection id="candidate-education" icon={GraduationCap} title="Educational details" subtitle="Course, passed year and institute, newest first." aside={sectionMissing('education')}>
                <EntryRowsEditor kind="education" value={form.education} onChange={(value) => set('education', value)} source={sources.education} suggestions={{ level: labels(OPT.educationLevel), institute: served.institutes }} />
              </FormSection>

              {/* ------------------------------------------------------------------ Work experience */}
              <FormSection id="candidate-experience" icon={BriefcaseBusiness} title="Previous experience" subtitle="Total, in India and abroad (whole years), then each employer." aside={sectionMissing('totalExperience', 'workHistory')}>
                <div className="ac-g">
                  {years('totalExperience', 'Total experience', 's-4')}
                  {years('experienceIndia', 'Experience in India', 's-4')}
                  {years('gulfExperience', 'Experience abroad', 's-4')}
                </div>
                <EntryRowsEditor kind="history" value={form.workHistory} onChange={(value) => set('workHistory', value)} source={sources.workHistory}
                  suggestions={{ company: companySuggestions, designation: designationSuggestions, country: countrySuggestions }} />
              </FormSection>

              {/* ------------------------------------------------------------------ Job preference */}
              <FormSection id="candidate-work" icon={Target} title="Job preference" subtitle="Job titles and skills help us search the Data Bank and match candidates to orders.">
                <div className="ac-g">
                  <div className="ac-f s-6" data-from={sources.designation ? '' : undefined}>
                    <TagField label="Job preference" value={form.designation} onChange={(value) => set('designation', value)} suggestions={designationSuggestions}
                      placeholder="Pick from the list, or type and press Enter" aux={sources.designation && <ReadBadge kind={sources.designation} />} />
                  </div>
                  <div className="ac-f s-6" data-from={sources.skills ? '' : undefined}>
                    <TagField label="Skills" value={form.skills} onChange={(value) => set('skills', value)} suggestions={skillSuggestions} placeholder="e.g. TIG welding, then Enter" aux={sources.skills && <ReadBadge kind={sources.skills} />} />
                  </div>
                  <div className="ac-f s-4">
                    <TagField label="Country looking for" value={form.countryPreference} onChange={(value) => set('countryPreference', value)} suggestions={countrySuggestions} placeholder="Pick a country, or type one" />
                  </div>
                  {choice('jobDurationPreference', 'Job duration preference', OPT.jobDuration, 's-4', 'Not discussed')}
                  {F('expectedSalary', 'Expected salary', <Input type="number" min="0" step="0.01" value={form.expectedSalary} onChange={(e) => set('expectedSalary', e.target.value)} placeholder="Per month" />, { cls: 's-4' })}
                </div>
              </FormSection>

              {/* ------------------------------------------------------------------ Assessment */}
              <FormSection id="candidate-assessment" icon={Gauge} title="Assessment & preferences" subtitle="From the screening conversation. The recruiter still records the initial call on the profile.">
                <div className="ac-g">
                  {F('serviceChargesStatus', 'Service charges', <FormOptionSelect category={OPT.serviceCharges} value={form.serviceChargesStatus} onChange={(value) => set('serviceChargesStatus', value)} label="Service charges" allowAdd />, { cls: 's-4' })}
                  <EntryField label="Interest level" className="s-12" hint="Tap the chosen card again to clear it.">
                    <InterestCards value={form.interestLevel} options={choicesFor(formOptions, OPT.interestLevel, form.interestLevel)} onChange={(value) => set('interestLevel', value)} />
                  </EntryField>
                </div>
              </FormSection>

              {/* ------------------------------------------------------------------ Source, follow-up, assignment */}
              <FormSection id="candidate-source" icon={Share2} title="Source & follow-up" subtitle="Where the candidate came from, who looks after them and when to follow up. The client, country and job come from the order once the candidate is matched.">
                <div className="ac-g">
                  <EntryField label="Source" className="s-3">
                    {/* "Not recorded" is saved as the catch-all source "Other" (the column cannot be empty); the list is the admin-managed one. */}
                    <FormOptionSelect category={OPT.source} value={form.source} onChange={(value) => set('source', value)} label="Source" allowClear clearLabel="Not recorded" allowAdd />
                  </EntryField>
                  <EntryField label={sourceHint} className="s-3" aux={agencyNames.length > 0 ? <datalist id="reference-agency-names">{[...new Set(agencyNames)].map((name) => <option key={name} value={name} />)}</datalist> : undefined}>
                    <Input value={form.sourceDetail} onChange={(e) => set('sourceDetail', e.target.value)} placeholder={sourcePlaceholder} maxLength={255} list={agencyNames.length ? 'reference-agency-names' : undefined} />
                  </EntryField>
                  {picksRecruiter && <EntryField label="Recruiter" className="s-3" hint={RECRUITER_FIELD_HINT}>
                    <Select value={form.assignTo} searchable onChange={(e) => set('assignTo', e.target.value)}>
                      <option value="">{NOT_ASSIGNED_YET}</option>
                      {recruiterOptions.map((user) => <option key={user.id} value={user.id}>{user.label}</option>)}
                    </Select>
                  </EntryField>}
                  <EntryField label="Next follow-up date" className="s-3" hint={followUpHint(picksRecruiter, chosenRecruiterName)}>
                    <DatePicker value={form.nextFollowUp} onChange={(value) => set('nextFollowUp', value)} />
                  </EntryField>
                </div>
              </FormSection>
            </fieldset>

            {!savedCandidateId && <footer className="ac-savebar">
              <div className="ac-savebar-copy">
                <span className={`ac-pill ${missing.length ? 'is-warn' : 'is-good'}`}><i aria-hidden />{missing.length ? 'Missing details' : 'Ready'}</span>
                <span>
                  <strong>{missing.length ? `Still needed: ${missing.join(' and ')}.` : unfilled.length ? `Ready to save. No ${unfilled.join(' or ')} yet, so this candidate will be marked for review.` : 'Ready to save.'}</strong>
                  {' '}<span className="ac-savebar-note">We check the Data Bank for a duplicate first. {savebarStatus(picksRecruiter, chosenRecruiterName)}</span>
                </span>
              </div>
              <div className="ac-savebar-actions">
                <Button variant="ghost" disabled={saving} onClick={() => navigate('/data-bank')}>Cancel</Button>
                <Button icon={<ClipboardList />} onClick={() => setPreviewing(true)}>Application form</Button>
                <Button variant="primary" onClick={save} disabled={busyAny} title={`${saveLabel} (Ctrl+Enter)`}>
                  {saving ? 'Saving…' : reading ? 'Reading documents…' : saveLabel}
                </Button>
              </div>
            </footer>}
          </div>

        </div>
      </div>

      {previewing && <Suspense fallback={<Dialog open onClose={() => setPreviewing(false)} title="Application form preview">
        <p role="status" className="text-sm text-ink-500">Opening preview…</p>
      </Dialog>}>
        <EntryFormPreview open onClose={() => setPreviewing(false)} form={previewForm} savedCandidateId={savedCandidateId}
          onOpenSaved={() => { setPreviewing(false); openApplicationForm() }} />
      </Suspense>}
      <DuplicateDialog duplicate={duplicate} form={form} saving={saving} error={duplicate ? saveError : ''}
        onCancel={() => setDuplicate(null)} onOpen={(id) => { setDuplicate(null); navigate(`/candidates/${id}`) }} onMerge={(id) => void combine(id)} />
    </>
  )
}
