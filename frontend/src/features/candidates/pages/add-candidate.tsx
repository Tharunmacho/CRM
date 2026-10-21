import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, ArrowRight, Check, FileText, ScanLine, ShieldCheck, Sparkles, UserRound } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { api, ApiError, type OcrScan } from '@/shared/api/client'
import { Badge, PageHeader, Panel, PanelHeader } from '@/shared/ui/display'
import { Button, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { FileDrop } from '@/shared/ui/file-drop'
import { DatePicker } from '@/shared/ui/date-picker'
import { Dialog } from '@/shared/ui/overlay'
import { RecordIdentity } from '@/shared/ui/workspace'
import { hasOcrExtraction, inspectedPageCount, mergeOcrForm, scanWarnings, type DocumentScans, type ScanKind } from '../ocr-form'
import { normalizedSkills } from '../candidate-skills'
import { scansFromJobs, useDocumentIntake } from '../use-document-intake'
import { IntakeRecovery } from '../components/intake-recovery'

const EMPTY = {
  name: '',
  phone: '',
  whatsapp: '',
  email: '',
  dob: '',
  gender: '',
  nationality: 'Indian',
  currentCity: '',
  languages: '',
  passportNo: '',
  passportIssue: '',
  passportExpiry: '',
  passportPlace: '',
  designation: '',
  totalExperience: '',
  gulfExperience: '',
  skills: '',
  expectedSalary: '',
  source: 'walk_in',
}

type FormKey = keyof typeof EMPTY
type Duplicate = { id: string; ref: string; name: string; phone: string; ownerId: string | null; ownerName: string | null; matchedOn: string[] }
const SCAN_KINDS: ScanKind[] = ['resume', 'passport', 'aadhaar']

function DocumentReadReport({ kind, scan }: { kind: ScanKind; scan: OcrScan }) {
  const label = kind === 'resume' ? 'Resume' : kind === 'passport' ? 'Passport' : 'Aadhaar'
  const inspection = scan.inspection
  const extracted = hasOcrExtraction(scan)
  const warnings = scanWarnings(scan)
  const selected = inspection?.selected_pages
    ?? (kind === 'resume' ? inspection?.resume_pages : kind === 'passport' ? inspection?.passport_pages : inspection?.aadhaar_pages)
  const list = (pages: number[] | undefined) => pages?.length ? pages.join(', ') : 'None'
  const title = scan.extraction_status === 'no_resume'
    ? 'No resume identified'
    : !extracted ? 'Needs review' : 'Extraction returned'

  return (
    <section
      aria-label={`${label} reading result`}
      className={`rounded-control border px-3 py-3 text-xs ${extracted ? 'border-navy-100 bg-navy-50 text-navy-700' : 'border-amber-200 bg-amber-50 text-amber-900'}`}
    >
      <p className="font-semibold">{label}: {title}</p>
      <p className="mt-1">
        {extracted
          ? 'Review the suggested details before saving. Fields you edited have been kept.'
          : 'No extracted details were applied from this document. Review the report, try a clearer file, or enter the details manually.'}
      </p>
      {inspection && (
        <>
          <dl className="mt-3 grid gap-x-4 gap-y-2 sm:grid-cols-2">
            <div><dt className="font-medium">Pages inspected</dt><dd>{inspectedPageCount(inspection)} of {inspection.page_count}</dd></div>
            <div><dt className="font-medium">Pages selected for {kind} extraction</dt><dd className="break-words">{list(selected)}</dd></div>
            <div><dt className="font-medium">Resume pages identified</dt><dd className="break-words">{list(inspection.resume_pages)}</dd></div>
            <div><dt className="font-medium">Unread pages</dt><dd className="break-words">{list(inspection.unread_pages)}</dd></div>
            <div><dt className="font-medium">Uncertain page types</dt><dd className="break-words">{list(inspection.unknown_pages)}</dd></div>
          </dl>
          <p className="mt-2">Page numbers refer to the original document. Inspected pages can still be unread or uncertain.</p>
          {inspection.pages.length > 0 && (
            <details className="mt-3">
              <summary className="cursor-pointer font-medium">Review individual pages</summary>
              <ol className="mt-2 max-h-64 space-y-2 overflow-y-auto pr-2">
                {inspection.pages.map((page) => (
                  <li key={page.page_number} className="rounded border border-current/10 bg-white/60 px-2 py-1.5">
                    <span className="font-medium">Page {page.page_number}</span>
                    {' — '}{page.status}; {page.document_type === 'unknown' ? 'uncertain type' : page.document_type}
                    {page.reason && <p className="mt-1">{page.reason}</p>}
                  </li>
                ))}
              </ol>
            </details>
          )}
        </>
      )}
      {warnings.length > 0 && (
        <ul className="mt-3 space-y-1">
          {warnings.map((warning, index) => (
            <li key={index} className="flex items-start gap-2 text-amber-900">
              <AlertTriangle aria-hidden className="mt-px h-3.5 w-3.5 shrink-0 text-amber-700" />
              <span>{warning}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export default function AddCandidatePage() {
  const store = useStore()
  const navigate = useNavigate()
  const intake = useDocumentIntake(store.me!.id)
  const [form, setForm] = useState(EMPTY)
  const [saving, setSaving] = useState(false)
  const [savedCandidateId, setSavedCandidateId] = useState<string | null>(null)
  const [uploadErrors, setUploadErrors] = useState<Record<string, string>>({})
  const [saveError, setSaveError] = useState('')

  const [duplicate, setDuplicate] = useState<{ candidate: Duplicate; matchedOn: string[] } | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})

  // Inspection can succeed without extraction, so it has its own visible result.
  const [scans, setScans] = useState<DocumentScans>({})
  const scansRef = useRef<DocumentScans>({})
  const edited = useRef(new Set<FormKey>())
  const readController = useRef<AbortController | null>(null)
  const [readErrors, setReadErrors] = useState<Partial<Record<ScanKind, string>>>({})
  const [readMessage, setReadMessage] = useState('')
  const [reading, setReading] = useState(false)

  useEffect(() => () => {
    readController.current?.abort()
    readController.current = null
  }, [])

  const set = (k: FormKey, v: string) => {
    // A ref also catches changes made after a scan starts but before it returns.
    edited.current.add(k)
    setForm((f) => ({ ...f, [k]: v }))
    setErrors((e) => ({ ...e, [k]: '' }))
  }

  const snapshotDraft = (candidateId = savedCandidateId) => ({ form, editedFields: [...edited.current], savedCandidateId: candidateId })
  const draftRef = useRef(snapshotDraft())
  draftRef.current = snapshotDraft()
  useEffect(() => {
    if (!intake.jobs.length) return
    const timer = setTimeout(() => { void intake.saveDraft(draftRef.current).catch(() => {}) }, 700)
    return () => clearTimeout(timer)
  }, [form, intake.jobs.length, savedCandidateId])

  /** Originals are durable before reading; choosing a replacement keeps human edits. */
  const choose = (kind: ScanKind) => async (file: File) => {
    if (readController.current || saving || savedCandidateId) return
    await intake.upload(kind, file)
    if (!store.isCurrentSession()) return
    const { [kind]: _, ...remaining } = scansRef.current
    scansRef.current = remaining
    setScans(remaining)
    setForm((previous) => mergeOcrForm(previous, EMPTY, remaining, edited.current))
    setReadErrors(({ [kind]: _, ...rest }) => rest)
    setReadMessage('Original saved. You can leave this page and resume this intake later.')
    await intake.saveDraft(draftRef.current).catch(() => {})
  }

  const chosen = intake.jobs.filter((job) => SCAN_KINDS.some((kind) => job.kind === kind)).map((job) => job.kind)
  const recoverIntake = async (groupId: string) => {
    if (saving || reading || intake.uploading) return
    try {
      const recovered = await intake.recover(groupId)
      const read = scansFromJobs(recovered.jobs)
      const draft = recovered.draft
      const savedForm = { ...EMPTY, ...(draft?.form ?? {}) }
      const recoveredEdits = new Set<FormKey>((draft?.editedFields ?? []).filter((key): key is FormKey => Object.hasOwn(EMPTY, key)))
      // Current unsaved human edits win when a saved intake is opened.
      for (const key of edited.current) { savedForm[key] = form[key]; recoveredEdits.add(key) }
      edited.current = recoveredEdits
      scansRef.current = read
      setScans(read)
      setForm(mergeOcrForm(savedForm, EMPTY, read, edited.current))
      setSavedCandidateId(draft?.savedCandidateId ?? recovered.jobs.find((job) => job.candidateId)?.candidateId ?? null)
      setReadErrors({}); setSaveError('')
      setReadMessage('Saved originals, reading progress and your form draft have been restored.')
    } catch { /* The intake panel keeps the recovery error visible. */ }
  }

  const readDocuments = async () => {
    if (readController.current || saving || savedCandidateId || intake.uploading || chosen.length === 0) return
    const controller = new AbortController()
    readController.current = controller
    setReading(true); setReadErrors({}); setReadMessage('')
    try {
      await intake.saveDraft(snapshotDraft())
      const jobs = await intake.read(controller.signal)
      if (readController.current !== controller || !store.isCurrentSession()) return
      const read = scansFromJobs(jobs)
      const failures: Partial<Record<ScanKind, string>> = {}
      for (const job of jobs) if (job.status === 'failed' && SCAN_KINDS.some((kind) => job.kind === kind)) failures[job.kind as ScanKind] = job.lastError ?? 'Reading failed. Retry the saved original or enter details manually.'
      scansRef.current = read; setScans(read); setReadErrors(failures)
      setForm((previous) => mergeOcrForm(previous, EMPTY, read, edited.current))
      setErrors((previous) => Object.fromEntries(Object.entries(previous).filter(([key]) => edited.current.has(key as FormKey))))
    } catch (error) {
      if (readController.current === controller && store.isCurrentSession()) setReadMessage(error instanceof Error ? error.message : 'Reading could not finish. Your original and progress are saved.')
    } finally {
      if (readController.current === controller) { readController.current = null; setReading(false) }
    }
  }

  const cancelReading = () => {
    readController.current?.abort()
    readController.current = null
    setReading(false)
    setForm((prev) => mergeOcrForm(prev, EMPTY, scansRef.current, edited.current))
    setReadMessage('Stopped waiting. Your saved reading continues in the background; resume it later or enter details manually.')
  }

  const scanned = SCAN_KINDS.filter((kind) => hasOcrExtraction(scans[kind]))
  const ocr = Object.fromEntries(scanned.map((kind) => [kind, scans[kind]!.raw]))

  const save = async () => {
    if (saving || reading || intake.uploading) return
    const next: Record<string, string> = {}
    if (!form.name.trim()) next.name = 'A name is needed to save the record.'
    if (!form.phone.trim()) next.phone = 'A phone number is needed — it is how duplicates are caught.'
    for (const field of ['totalExperience', 'gulfExperience'] as const) {
      if (form[field] && (!Number.isInteger(Number(form[field])) || Number(form[field]) < 0)) {
        next[field] = 'Enter a whole number of years, zero or more.'
      }
    }
    if (form.expectedSalary && (!Number.isFinite(Number(form.expectedSalary)) || Number(form.expectedSalary) < 0)) {
      next.expectedSalary = 'Enter an amount of zero or more.'
    }
    setErrors(next)
    if (Object.keys(next).length) return

    setSaving(true)
    setSaveError('')
    try {
      if (savedCandidateId) await uploadDocuments(savedCandidateId)
      else await doSave()
    } catch (error) {
      if (error instanceof ApiError && error.status === 409 && typeof error.detail === 'object' && error.detail && 'candidate' in error.detail) {
        const candidate = (error.detail as { candidate: Duplicate }).candidate
        setDuplicate({ candidate, matchedOn: candidate.matchedOn })
      }
      if (store.isCurrentSession()) setSaveError(error instanceof Error ? error.message : 'The candidate could not be saved. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  const doSave = async () => {
    const result = await api.post<{ duplicate: boolean; candidate?: Duplicate }>('/candidates/check-duplicate', payload())
    if (result.duplicate && result.candidate) {
      setDuplicate({ candidate: result.candidate, matchedOn: result.candidate.matchedOn })
      return
    }
    await commit()
  }

  const payload = () => ({
      name: form.name.trim(),
      phone: form.phone.trim(),
      whatsapp: form.whatsapp.trim() || undefined,
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
      source: form.source,
      // The server prefers the scanned resume's own text; this covers a hand-filled form.
      resume_text: `${form.name} — ${form.designation}, ${form.totalExperience || 0} years. ${form.skills}`,
      ocr_data: scanned.length ? ocr : undefined,
    })

  const commit = async (mergeId?: string) => {
    const created = mergeId ? await api.post<{ id: string }>(`/candidates/${mergeId}/merge`, payload()) : await store.addCandidate(payload())
    if (!created) return
    setSavedCandidateId(created.id)
    setDuplicate(null)
    await intake.saveDraft(snapshotDraft(created.id))
    await uploadDocuments(created.id)
  }

  const uploadDocuments = async (candidateId: string) => {
    await intake.saveDraft(snapshotDraft(candidateId))
    const failed: Record<string, string> = await intake.attach(candidateId)
    if (!store.isCurrentSession()) return
    setUploadErrors(failed)
    if (!Object.keys(failed).length) await openSavedProfile(candidateId)
  }

  const openSavedProfile = async (candidateId: string) => {
    // Read after uploads so the profile contains the newly saved original files.
    // A failed read leaves this recovery screen mounted with the saved record ID.
    await store.refresh({ keepCurrent: true })
    if (store.isCurrentSession()) navigate(`/candidates/${candidateId}`)
  }

  return (
    <>
      <PageHeader
        title="Add candidate"
        eyebrow="Grow your talent pool"
        description="A great opportunity starts with a great profile. Upload documents or enter the details below."
        actions={<Badge tone="navy"><UserRound size={14} /> New candidate profile</Badge>}
      />

      <div className="candidate-form-layout">
        <aside className="candidate-form-guide">
          <Panel>
            <span className="workspace-eyebrow">New profile</span>
            <RecordIdentity name={form.name.trim() || 'New candidate'} detail={form.phone || 'Add their details to get started'} />
            <nav aria-label="Candidate form sections">
              {[
                { id: 'candidate-documents', label: 'Documents', detail: 'Resume & passport scans', icon: FileText, complete: chosen.length > 0 },
                { id: 'candidate-basic', label: 'Basic details', detail: 'Identity & contact information', icon: UserRound, complete: Boolean(form.name.trim() && form.phone.trim()) },
                { id: 'candidate-passport', label: 'Passport details', detail: 'Travel document information', icon: ShieldCheck, complete: Boolean(form.passportNo.trim()) },
                { id: 'candidate-work', label: 'Work profile', detail: 'Skills, experience & source', icon: UserRound, complete: Boolean(form.skills.trim() || form.designation.trim()) },
              ].map(({ id, label, detail, icon: Icon, complete }) => (
                <a href={`#${id}`} key={id} className={complete ? 'is-filled' : ''}>
                  <span>{complete ? <Check size={17} /> : <Icon size={17} />}</span>
                  <span><strong>{label}</strong><small>{detail}</small></span>
                  <ArrowRight size={14} />
                </a>
              ))}
            </nav>
          </Panel>
          <div className="candidate-guide-tip"><ScanLine size={21} /><h2>Less typing, more recruiting</h2><p>Upload a resume and passport, then use Read documents to fill in the details. Review everything before saving.</p></div>
        </aside>
        <div className="candidate-form-sections space-y-4">
          <IntakeRecovery intake={intake} disabled={saving || reading || intake.uploading} onRecover={recoverIntake} onSaveDraft={() => intake.saveDraft(snapshotDraft())} />
          {saveError && <p role="alert" className="rounded-control border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">{saveError}</p>}
          {savedCandidateId && (Object.keys(uploadErrors).length > 0 || saveError) && <Panel>
            <div role="alert">
              <h2 className="font-semibold">{Object.keys(uploadErrors).length ? 'Candidate saved. Some documents still need attaching.' : 'Candidate saved. Finish the remaining intake steps.'}</h2>
              <p className="mt-1 text-sm text-ink-500">Keep this page open to retry. Retrying uses the same candidate record and keeps successful uploads.</p>
              <ul className="my-3 space-y-1 text-sm text-amber-900">{Object.entries(uploadErrors).map(([slot, error]) => <li key={slot}>{error}</li>)}</ul>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="primary" disabled={saving} onClick={save}>{saving ? 'Please wait…' : Object.keys(uploadErrors).length ? 'Retry failed uploads' : 'Retry saved intake'}</Button>
              {Object.keys(uploadErrors).length > 0 && <Button disabled={saving} onClick={async () => {
                setSaving(true)
                try { await openSavedProfile(savedCandidateId) }
                catch (error) { if (store.isCurrentSession()) setSaveError(error instanceof Error ? error.message : 'The profile could not refresh. Please retry.') }
                finally { setSaving(false) }
              }}>Open saved profile</Button>}
            </div>
            {Object.keys(uploadErrors).length > 0 && <p className="mt-2 text-xs text-ink-500">Your originals remain in Saved document intake. Resume this intake to retry attaching them.</p>}
          </Panel>}
          <fieldset disabled={saving || Boolean(savedCandidateId)} className="min-w-0 space-y-4">
          <Panel flush id="candidate-documents" className="candidate-form-section">
            <PanelHeader title={<span className="form-section-title"><span>01</span>Documents</span>} meta="Upload first. Let document reading do the heavy lifting." />
            <div className="grid gap-4 px-5 py-5 sm:grid-cols-2">
              {SCAN_KINDS.map((kind) => {
                const job = intake.jobs.find((item) => item.kind === kind)
                return <FileDrop key={kind} label={kind === 'resume' ? 'Resume' : kind === 'passport' ? 'Passport copy' : 'Aadhaar copy'}
                  value={job ? { id: job.id, name: job.filename, sizeKb: job.sizeKb, url: job.originalUrl } : null}
                  onFile={choose(kind)} disabled={reading || saving || intake.uploading} busy={reading && !!job} busyLabel="reading…"
                  help="The original is saved immediately. Choose Read documents to extract details." />
              })}
              {([
                { slot: 'photo', label: 'Photo' },
                { slot: 'education', label: 'Education certificates' },
                { slot: 'experience', label: 'Experience certificates' },
              ] as const).map(({ slot, label }) => {
                const job = intake.jobs.find((item) => item.kind === slot)
                return <FileDrop key={slot} label={label} disabled={saving || intake.uploading || reading || Boolean(savedCandidateId)}
                  value={job ? { id: job.id, name: job.filename, sizeKb: job.sizeKb, url: job.originalUrl } : null}
                  help="The original is saved immediately and attached when you save the candidate."
                  onFile={async (file) => { await intake.upload(slot, file); await intake.saveDraft(draftRef.current).catch(() => {}) }} />
              })}
            </div>
            <div className="document-read-actions flex flex-wrap items-center gap-3 px-5 pb-5">
              <Button
                onClick={readDocuments}
                disabled={reading || saving || intake.uploading || chosen.length === 0}
                icon={<Sparkles className="h-4 w-4" />}
              >
                {reading ? 'Reading…' : 'Read documents'}
              </Button>
              {reading && <Button variant="ghost" onClick={cancelReading}>Continue manually</Button>}
              <span className="text-xs text-ink-400">
                {reading
                  ? 'Reading continues in the background if you leave. Choose Continue manually to stop waiting.'
                  : 'Your edits are kept on every read. For unedited identity fields, passport details take priority.'}
              </span>
            </div>
            <div aria-live="polite" aria-atomic="false" className="mx-5 mb-5 space-y-3">
              {readMessage && <p className="text-xs text-ink-500">{readMessage}</p>}
              {SCAN_KINDS.map((kind) => (
                readErrors[kind] ? (
                  <p key={kind} role="alert" className="rounded-control border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs text-amber-900">
                    <strong>{kind === 'resume' ? 'Resume' : kind === 'passport' ? 'Passport' : 'Aadhaar'}:</strong> {readErrors[kind]} You can enter the details and save manually.
                  </p>
                ) : scans[kind] ? <DocumentReadReport key={kind} kind={kind} scan={scans[kind]!} /> : null
              ))}
            </div>
          </Panel>

          <Panel flush id="candidate-basic" className="candidate-form-section">
            <PanelHeader title={<span className="form-section-title"><span>02</span>Basic details</span>} meta="The essentials that help your team stay in touch." />
            <div className="grid gap-4 px-4 py-4 sm:grid-cols-2">
              <Field label="Full name" required error={errors.name} className="sm:col-span-2">
                <Input value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="As written in the passport" />
              </Field>
              <Field label="Phone" required error={errors.phone}>
                <Input ident value={form.phone} onChange={(e) => set('phone', e.target.value)} placeholder="+91 98400 00000" />
              </Field>
              <Field label="Email">
                <Input type="email" value={form.email} onChange={(e) => set('email', e.target.value)} />
              </Field>
              <Field label="WhatsApp number">
                <Input ident value={form.whatsapp} onChange={(e) => set('whatsapp', e.target.value)} />
              </Field>
              <Field label="Current city">
                <Input value={form.currentCity} onChange={(e) => set('currentCity', e.target.value)} />
              </Field>
              <Field label="Date of birth">
                <DatePicker value={form.dob} onChange={(value) => set('dob', value)} />
              </Field>
              <Field label="Gender">
                <Select value={form.gender} onChange={(e) => set('gender', e.target.value)}>
                  <option value="">Not recorded</option>
                  <option value="male">Male</option>
                  <option value="female">Female</option>
                </Select>
              </Field>
              <Field label="Nationality">
                <Input value={form.nationality} onChange={(e) => set('nationality', e.target.value)} />
              </Field>
              <Field label="Languages" hint="Separate with commas">
                <Input value={form.languages} onChange={(e) => set('languages', e.target.value)} placeholder="English, Hindi" />
              </Field>
            </div>
          </Panel>

          <Panel flush id="candidate-passport" className="candidate-form-section">
            <PanelHeader title={<span className="form-section-title"><span>03</span>Passport details</span>} meta="Needed before stage 7. Collect it early if you can." />
            <div className="grid gap-4 px-4 py-4 sm:grid-cols-2">
              <Field label="Passport number">
                <Input ident value={form.passportNo} onChange={(e) => set('passportNo', e.target.value)} placeholder="M1234567" />
              </Field>
              <Field label="Place of issue">
                <Input value={form.passportPlace} onChange={(e) => set('passportPlace', e.target.value)} />
              </Field>
              <Field label="Issue date">
                <DatePicker value={form.passportIssue} onChange={(value) => set('passportIssue', value)} />
              </Field>
              <Field label="Expiry date">
                <DatePicker value={form.passportExpiry} onChange={(value) => set('passportExpiry', value)} />
              </Field>
            </div>
          </Panel>

          <Panel flush id="candidate-work" className="candidate-form-section">
            <PanelHeader title={<span className="form-section-title"><span>04</span>Work profile & source</span>} meta="Review extracted skills and experience before saving." />
            <div className="grid gap-4 px-4 py-4 sm:grid-cols-2">
              <Field label="Preferred designations" hint="Separate multiple job titles with commas." className="sm:col-span-2">
                <Input list="candidate-designations" value={form.designation} onChange={(e) => set('designation', e.target.value)} placeholder="Welder, Fabricator" />
                <datalist id="candidate-designations">{store.designations.map((designation) => <option key={designation.id} value={designation.title} />)}</datalist>
              </Field>
              <Field label="Total experience (years)" error={errors.totalExperience}>
                <Input type="number" min="0" step="1" value={form.totalExperience} onChange={(e) => set('totalExperience', e.target.value)} />
              </Field>
              <Field label="Gulf / overseas experience (years)" error={errors.gulfExperience}>
                <Input type="number" min="0" step="1" value={form.gulfExperience} onChange={(e) => set('gulfExperience', e.target.value)} />
              </Field>
              <Field label="Skills" hint="Separate skills with commas or new lines. These skills are used in Data Bank and order matching." className="sm:col-span-2">
                <Textarea value={form.skills} onChange={(e) => set('skills', e.target.value)} placeholder="TIG welding, Pipe fitting, Blueprint reading" />
              </Field>
              <Field label="Expected salary" error={errors.expectedSalary}>
                <Input type="number" min="0" step="0.01" value={form.expectedSalary} onChange={(e) => set('expectedSalary', e.target.value)} />
              </Field>
              <Field label="Source">
                <Select value={form.source} onChange={(e) => set('source', e.target.value)}>
                  <option value="walk_in">Walk-in</option><option value="referral">Referral</option><option value="email">Email</option><option value="whatsapp">WhatsApp</option><option value="other">Other</option>
                </Select>
              </Field>
            </div>
          </Panel>
          </fieldset>

          {!savedCandidateId && <div className="candidate-save-bar flex items-center justify-end gap-2 pb-2">
            <span><ShieldCheck size={16} /> Name and phone are required</span>
            <Button variant="ghost" disabled={saving} onClick={() => navigate('/data-bank')}>
              Cancel
            </Button>
            <Button variant="primary" onClick={save} disabled={saving || reading || intake.uploading}>
              {saving ? 'Saving…' : reading ? 'Reading documents…' : 'Save and assign'}
            </Button>
          </div>}
        </div>
      </div>

      <Dialog
        open={!!duplicate}
        onClose={() => { if (!saving) setDuplicate(null) }}
        title="Existing candidate found"
        description={
          duplicate
            ? `The ${duplicate.matchedOn.join(' and ')} already belong to a record in the Data Bank.`
            : undefined
        }
        footer={
          <>
            <Button variant="ghost" disabled={saving} onClick={() => setDuplicate(null)}>
              Cancel merge
            </Button>
            <Button
              disabled={saving}
              onClick={() => {
                const id = duplicate!.candidate.id
                setDuplicate(null)
                navigate(`/candidates/${id}`)
              }}
            >
              Open the existing record
            </Button>
            <Button variant="primary" disabled={saving || (!!duplicate && !store.can('work_any_candidate') && duplicate.candidate.ownerId !== store.me.id)} onClick={async () => {
              if (!duplicate) return
              setSaving(true); setSaveError('')
              try { await commit(duplicate.candidate.id) } catch (error) { setSaveError(error instanceof Error ? error.message : 'Merge failed. Your entry has been kept.') } finally { setSaving(false) }
            }}>{saving ? 'Merging…' : 'Merge into existing'}</Button>
          </>
        }
      >
        {duplicate && (
          <div className="rounded-control border border-rule bg-field/50 p-4">
            <p className="text-sm font-semibold text-ink">{duplicate.candidate.name}</p>
            <p className="mt-0.5 text-micro text-ink-400 ident">
              {duplicate.candidate.phone}
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <Badge>{duplicate.candidate.ref}</Badge>
              <Badge>Owner: {duplicate.candidate.ownerName ?? 'Unassigned'}</Badge>
            </div>
          </div>
        )}
        <p className="mt-3 flex items-start gap-2 text-xs text-ink-500">
          <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0 text-amber-600" />
          Merging fills empty profile fields and adds your documents as new versions. Existing details, ownership and workflow stay in place. Only the assigned recruiter or a supervisor can merge.
        </p>
        {saveError && <p role="alert" className="mt-3 text-sm text-stamp-600">{saveError}</p>}
      </Dialog>
    </>
  )
}
