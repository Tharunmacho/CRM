import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, ExternalLink, FileText, Plus, RefreshCw, Save, Users } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api, ApiError, fileHref } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { Button, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'

type SourceType = 'manual' | 'email' | 'whatsapp'
type PageRef = { sourceId: string; pageNumber: number; kind: string }
type Page = PageRef & { status: string; blocked: boolean; reason: string; snippet: string; previewUrl: string }
type Ignored = { sourceId: string; pageNumber: number; reason: string }
type Person = { id?: string; key: string; label: string; fields: Record<string, unknown>; pages: PageRef[]; candidateId?: string }
type Bundle = { id: string; status: string; revision: number; lastError?: string; sources: { id: string; name: string; pageCount?: number; originalUrl?: string }[]; pages: Page[]; groups: Omit<Person, 'key'>[]; ignoredPages: Ignored[]; candidateIds: string[] }
type Duplicate = { groupId: string; label: string; candidateId: string; ref: string; name: string; phone: string; matchedOn: string[]; canMerge: boolean }
const kindOptions = [ ['source_bundle', 'Other document'], ['resume', 'Resume'], ['passport', 'Passport'], ['aadhaar', 'Aadhaar'], ['photo', 'Photo'], ['education', 'Education'], ['experience', 'Experience'] ].map(([value, label]) => ({ value, label }))
const pageKey = (page: { sourceId: string; pageNumber: number }) => `${page.sourceId}:${page.pageNumber}`
const optionalFields: [string, string, string?][] = [
  ['whatsapp', 'WhatsApp number'], ['dob', 'Date of birth', 'date'], ['gender', 'Gender'], ['nationality', 'Nationality'],
  ['current_city', 'Current city'], ['languages', 'Languages (separate with commas)', 'list'], ['passport_issue', 'Passport issue date', 'date'],
  ['passport_expiry', 'Passport expiry date', 'date'], ['passport_place', 'Place of issue'], ['designations', 'Job titles (separate with commas)', 'list'],
  ['skills', 'Skills (separate with commas)', 'list'], ['total_experience', 'Total experience in years', 'number'], ['gulf_experience', 'Gulf experience in years', 'number'], ['expected_salary', 'Expected salary'],
]

type Props = {
  sourceType: SourceType; sourceId: string; onClose: () => void
  onCommitted?: (result: { bundleId: string; candidateIds: string[] }) => void | Promise<void>
}
export function DocumentBundleReview(props: Props) {
  const store = useStore()
  return <PersonReview key={`${props.sourceType}:${props.sourceId}:${store.me?.id}`} {...props} />
}
function PersonReview({ sourceType, sourceId, onClose, onCommitted }: Props) {
  const store = useStore()
  const [bundle, setBundle] = useState<Bundle | null>(null)
  const [people, setPeople] = useState<Person[]>([])
  const [ignored, setIgnored] = useState<Ignored[]>([])
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [uncertain, setUncertain] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [duplicates, setDuplicates] = useState<Duplicate[]>([])
  const [decisions, setDecisions] = useState<Record<string, string>>({})
  const [preview, setPreview] = useState<Page | null>(null)
  const [closing, setClosing] = useState(false)
  const alive = useRef(true)
  const gate = useRef(false)
  const notified = useRef('')
  const valid = () => alive.current && store.isCurrentSession()
  const apply = (value: Bundle) => {
    if (!valid()) return
    setBundle(value); setPeople(value.groups.map(person => ({ ...person, key: person.id || crypto.randomUUID() })))
    setIgnored(value.ignoredPages); setDirty(false); setUncertain(false); setConfirmed(false)
    setDuplicates([]); setDecisions({})
  }
  const finish = async (value: Bundle) => {
    apply(value)
    if (value.status === 'completed' && notified.current !== value.id && valid()) {
      notified.current = value.id
      try { await onCommitted?.({ bundleId: value.id, candidateIds: value.candidateIds }) }
      catch { if (valid()) setNotice('The candidates are saved. Reload this page to see them in the list.') }
    }
  }
  const start = async () => {
    if (gate.current) return
    gate.current = true; setBusy(true); setError('')
    try { await finish(await api.post<Bundle>('/document-bundles', { sourceType, sourceId })) }
    catch (err) { if (valid()) setError(err instanceof Error ? err.message : 'We could not open this saved review.') }
    finally { gate.current = false; if (valid()) setBusy(false) }
  }
  useEffect(() => {
    alive.current = true
    void start()
    return () => { alive.current = false }
  }, [sourceType, sourceId])
  useEffect(() => {
    if (bundle?.status !== 'pending' || bundle.lastError) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const value = await api.get<Bundle>(`/document-bundles/${bundle.id}`)
        if (cancelled || !valid()) return
        apply(value); setError('')
        if (value.status === 'pending' && !value.lastError) timer = setTimeout(poll, 2500)
      } catch (err) { if (!cancelled && valid()) setError(err instanceof Error ? err.message : 'We could not check progress. Reload the saved review.') }
    }
    timer = setTimeout(poll, 2500)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [bundle?.id, bundle?.status, bundle?.lastError])
  useEffect(() => {
    if (!dirty) return
    const preventLoss = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', preventLoss)
    return () => window.removeEventListener('beforeunload', preventLoss)
  }, [dirty])
  const reload = async () => {
    if (!bundle || gate.current) return
    if (dirty && !window.confirm('Reload the saved review? Changes you have not saved will be lost.')) return
    gate.current = true; setBusy(true); setError('')
    try { await finish(await api.get<Bundle>(`/document-bundles/${bundle.id}`)) }
    catch (err) { if (valid()) setError(err instanceof Error ? err.message : 'We could not load the saved review.') }
    finally { gate.current = false; if (valid()) setBusy(false) }
  }
  const changed = () => { setDirty(true); setConfirmed(false); setDuplicates([]); setDecisions({}); setNotice('') }
  const field = (key: string, name: string, value: unknown) => {
    setPeople(current => current.map(person => {
      if (person.key !== key) return person
      const fields = { ...person.fields }
      if (value === '') delete fields[name]
      else fields[name] = value
      return { ...person, fields, label: name === 'name' && typeof value === 'string' && value.trim() ? value.trim() : person.label }
    })); changed()
  }
  const assign = (page: Page, destination: string) => {
    const key = pageKey(page)
    setPeople(current => current.map(person => ({ ...person, pages: [
      ...person.pages.filter(item => pageKey(item) !== key),
      ...(person.key === destination ? [{ sourceId: page.sourceId, pageNumber: page.pageNumber, kind: kindOptions.some(item => item.value === page.kind) ? page.kind : 'source_bundle' }] : []),
    ] })))
    setIgnored(current => [...current.filter(item => pageKey(item) !== key), ...(destination === 'exclude' ? [{ sourceId: page.sourceId, pageNumber: page.pageNumber, reason: '' }] : [])])
    changed()
  }
  const save = async (closeAfter = false) => {
    if (!bundle || gate.current || uncertain) return
    gate.current = true; setBusy(true); setError('')
    try {
      const value = await api.put<Bundle>(`/document-bundles/${bundle.id}/review`, { revision: bundle.revision,
        groups: people.map(({ id, label, fields, pages }) => ({ id, label, fields, pages })), ignoredPages: ignored })
      apply(value); setNotice('Review saved. You can close this and carry on later.')
      if (closeAfter && valid()) onClose()
    } catch (err) {
      if (valid()) { setError(err instanceof Error ? err.message : 'We could not save the review.'); setUncertain(err instanceof ApiError && (err.status === 0 || err.status === 503 || err.status === 409)) }
    } finally { gate.current = false; if (valid()) setBusy(false) }
  }
  const commit = async () => {
    if (!bundle || gate.current || dirty || !confirmed || uncertain) return
    gate.current = true; setBusy(true); setError('')
    try { await finish(await api.post<Bundle>(`/document-bundles/${bundle.id}/commit`, { revision: bundle.revision, confirmed: true,
      duplicateDecisions: Object.entries(decisions).map(([groupId, candidateId]) => ({ groupId, candidateId })) })) }
    catch (err) {
      if (valid()) {
        setError(err instanceof Error ? err.message : 'We could not confirm that it saved.')
        const detail = err instanceof ApiError ? err.detail as { code?: string; duplicates?: Duplicate[] } | undefined : undefined
        if (detail?.code === 'duplicate_candidates') { setDuplicates(detail.duplicates || []); setDecisions({}) }
        else if (err instanceof ApiError && [0, 503, 409].includes(err.status)) setUncertain(true)
      }
    } finally { gate.current = false; if (valid()) setBusy(false) }
  }
  const close = () => { if (!gate.current) dirty ? setClosing(true) : onClose() }
  const assigned = new Map(people.flatMap(person => person.pages.map(page => [pageKey(page), person] as const)))
  const exclusions = new Map(ignored.map(page => [pageKey(page), page]))
  const remaining = bundle?.pages.filter(page => !assigned.has(pageKey(page)) && !exclusions.has(pageKey(page))).length || 0
  const incomplete = !people.length || remaining > 0 || ignored.some(page => !page.reason.trim()) || people.some(person => !person.pages.length || !String(person.fields.name || '').trim() || !String(person.fields.phone || '').trim())
  const locked = busy || uncertain || bundle?.status !== 'review'
  return <Dialog open onClose={close} width="full" title="Check people and pages" description="Sort the pages by person, check each person's details, then save all their profiles."
    footer={<div className="flex w-full flex-wrap items-center justify-between gap-3">
      <span className="text-sm text-ink-500">{dirty ? 'Changes not saved' : bundle?.status === 'completed' ? 'Candidates saved' : 'Your progress is kept with this upload'}</span>
      <div className="flex flex-wrap gap-2"><Button disabled={busy} onClick={close}>{bundle?.status === 'completed' ? 'Done' : 'Close'}</Button>
        {bundle?.status === 'review' && <><Button disabled={locked || !dirty} onClick={() => void save()} icon={<Save size={16} />}>Save review</Button><Button variant="primary" disabled={locked || dirty || incomplete || !confirmed || duplicates.some(item => !decisions[item.groupId])} onClick={() => void commit()}>Confirm and save {people.length} {people.length === 1 ? 'person' : 'people'}</Button></>}
      </div></div>}>
    <div className="mx-auto max-w-[1440px] space-y-6">
      {error && <div role="alert" className="rounded-panel bg-stamp-50 p-4 text-sm text-stamp-700">{error}<div className="mt-3 flex flex-wrap gap-2"><Button disabled={busy} onClick={() => void (bundle ? reload() : start())} icon={<RefreshCw size={16} />}>{bundle ? 'Reload saved review' : 'Open saved review'}</Button></div>{uncertain && <p className="mt-2">Your save may have worked, or someone else may have changed this review. Reload it before you try again.</p>}</div>}
      {notice && <p role="status" className="rounded-panel bg-navy-50 p-4 text-sm text-navy-700">{notice}</p>}
      {!bundle && !error && <p role="status">Opening your saved review…</p>}
      {bundle?.status === 'pending' && <section className="rounded-panel border border-rule bg-field p-6"><FileText className="mb-3 text-navy-600" /><h3 className="font-semibold">{bundle.lastError ? 'We could not get the pages ready' : 'Getting the pages ready'}</h3><p className="mt-2 text-sm text-ink-500">{bundle.lastError || 'We are checking your saved files. You can close this and come back. We keep working.'}</p>{bundle.lastError && <Button className="mt-4" disabled={busy} onClick={() => void start()}>Try again</Button>}</section>}
      {bundle?.status === 'completed' && <section className="rounded-panel border border-emerald-200 bg-emerald-50 p-6"><CheckCircle2 className="mb-3 text-emerald-600" /><h3 className="text-lg font-semibold">{bundle.candidateIds.length} candidate profiles saved</h3><p className="mt-2 text-sm">Each person got only the pages you picked for them. Candidates who already existed keep their recruiter and deadlines.</p><div className="mt-4 grid gap-3 sm:grid-cols-2">{bundle.groups.map(person => <Link onClick={onClose} key={person.id} className="rounded-control border border-emerald-200 bg-white p-4 font-medium text-navy-700" to={`/candidates/${person.candidateId}`}>{person.label} <span className="block text-sm font-normal">Open candidate profile →</span></Link>)}</div></section>}
      {bundle?.status === 'completed' && <section className="rounded-panel border border-rule p-5"><h3 className="font-semibold">Original uploads</h3><p className="mt-2 text-sm text-ink-500">These are the full files exactly as uploaded. They may hold several people. Each profile has only that person's pages.</p><ul className="mt-4 space-y-3">{bundle.sources.filter(source => source.originalUrl).map(source => <li key={source.id}><a className="inline-flex max-w-full items-start gap-2 text-sm font-medium text-navy-700" href={fileHref(`${source.originalUrl}?download=true`)}><FileText size={16} className="mt-0.5 shrink-0" /><span className="break-words">Download original: {source.name}</span></a></li>)}</ul></section>}
      {bundle?.status === 'review' && <>
        <section className="rounded-panel border border-rule bg-field p-5"><div className="flex flex-wrap items-start justify-between gap-4"><div><h3 className="font-semibold">1. Check the people we found</h3><p className="mt-2 max-w-3xl text-sm text-ink-500">We took names and phone numbers from labels we could read. Check every preview. Pages that are not clear stay unsorted, because page order alone does not tell us who a page belongs to.</p></div><Button disabled={locked} onClick={() => { setPeople(current => [...current, { key: crypto.randomUUID(), label: `Person ${current.length + 1}`, fields: {}, pages: [] }]); changed() }} icon={<Plus size={16} />}>Add person</Button></div><div className="mt-4 flex flex-wrap gap-3 text-sm"><span className="rounded-control bg-white px-3 py-2">{people.length} people</span><span className="rounded-control bg-white px-3 py-2">{bundle.pages.length} pages</span><span className="rounded-control bg-white px-3 py-2">{remaining} not sorted yet</span><span className="rounded-control bg-white px-3 py-2">{ignored.length} left out</span></div></section>
        <div className="grid gap-5 lg:grid-cols-2">{people.map((person, index) => <section key={person.key} className="min-w-0 rounded-panel border border-rule p-5"><div className="mb-4 flex items-start justify-between gap-3"><div className="flex min-w-0 items-center gap-3"><Users size={20} className="shrink-0 text-navy-600" /><h3 className="break-words font-semibold">Person {index + 1} · {person.label}</h3></div><Button size="sm" variant="dangerGhost" disabled={locked} onClick={() => { setPeople(current => current.filter(item => item.key !== person.key)); changed() }}>Remove</Button></div><p className="mb-4 text-sm text-ink-500">{person.pages.length} pages given to this person. If you remove them, their pages go back to not sorted yet.</p><div className="grid gap-4 sm:grid-cols-2">{[['name', 'Full name *'], ['phone', 'Phone *'], ['email', 'Email'], ['passport_no', 'Passport number']].map(([key, label]) => <Field key={key} label={label}><Input disabled={locked} value={String(person.fields[key] || '')} onChange={event => field(person.key, key, event.target.value)} /></Field>)}</div>{sourceType === 'manual' && <Field label="Candidate source" className="mt-4"><Select disabled={locked} value={String(person.fields.source || 'walk_in')} onChange={event => field(person.key, 'source', event.target.value)}><option value="walk_in">Walk-in</option><option value="referral">Referral</option><option value="other">Other</option><option value="email">Email</option><option value="whatsapp">WhatsApp</option></Select></Field>}<details className="mt-4"><summary className="cursor-pointer text-sm font-medium text-navy-700">More details</summary><div className="mt-4 grid gap-4 sm:grid-cols-2">{optionalFields.map(([key, label, type]) => <Field key={key} label={label}><Input disabled={locked} type={type === 'date' || type === 'number' ? type : 'text'} min={type === 'number' ? 0 : undefined} max={type === 'number' ? 80 : undefined} value={Array.isArray(person.fields[key]) ? (person.fields[key] as string[]).join(', ') : String(person.fields[key] ?? '')} onChange={event => field(person.key, key, type === 'list' ? event.target.value.split(',').map(value => value.trim()).filter(Boolean) : type === 'number' && event.target.value !== '' ? Number(event.target.value) : event.target.value)} /></Field>)}</div></details></section>)}</div>
        <section><h3 className="mb-2 font-semibold">2. Give every page to one person</h3><p className="mb-4 text-sm text-ink-500">Pick the person and the type of document. Or leave a page out and say why. A page with several people must be left out and uploaded again as separate pages.</p><div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]"><div className="space-y-3">{bundle.pages.map(page => { const key = pageKey(page); const owner = assigned.get(key); const excluded = exclusions.get(key); const selection = owner?.pages.find(item => pageKey(item) === key); return <article key={key} className="rounded-panel border border-rule p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0 flex-1"><h4 className="break-words font-medium">{bundle.sources.find(source => source.id === page.sourceId)?.name} · Page {page.pageNumber}</h4><p className="mt-1 text-sm text-ink-500">{page.blocked ? 'Several people on one page, so it cannot be used' : page.status === 'read' ? 'We could read this page. Check who it belongs to.' : 'We could not read this page well. Look at the preview yourself.'}</p></div><Button size="sm" onClick={() => setPreview(page)}>Preview</Button></div>{page.reason && <p className="mt-2 text-sm text-ink-500">{page.reason}</p>}<div className="mt-4 grid gap-3 sm:grid-cols-2"><Field label="Page belongs to"><Select disabled={locked} value={owner?.key || (excluded ? 'exclude' : '')} onChange={event => assign(page, event.target.value)}><option value="">Not sorted yet - pick a person</option>{!page.blocked && people.map((person, index) => <option key={person.key} value={person.key}>Person {index + 1}: {person.label}</option>)}<option value="exclude">Leave this page out</option></Select></Field>{owner && <Field label="Document type"><Select disabled={locked} value={selection?.kind || 'source_bundle'} onChange={event => { const kind = event.target.value; setPeople(current => current.map(person => ({ ...person, pages: person.pages.map(item => pageKey(item) === key ? { ...item, kind } : item) }))); changed() }}>{kindOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</Select></Field>}</div>{excluded && <Field label="Why is this page left out?" className="mt-3"><Textarea disabled={locked} value={excluded.reason} maxLength={500} onChange={event => { setIgnored(current => current.map(item => pageKey(item) === key ? { ...item, reason: event.target.value } : item)); changed() }} placeholder="For example: a cover sheet, or a page with several people that must be uploaded again on its own" /></Field>}</article> })}</div><aside className="min-w-0 rounded-panel border border-rule bg-field p-4 xl:sticky xl:top-0">{preview ? <><div className="mb-3 flex flex-wrap items-center justify-between gap-3"><h4 className="font-medium">Page {preview.pageNumber} preview</h4><a className="inline-flex items-center gap-2 text-sm text-navy-700" href={fileHref(preview.previewUrl)} target="_blank" rel="noreferrer">Open full preview <ExternalLink size={14} /></a></div><iframe title={`Original page ${preview.pageNumber}`} src={fileHref(preview.previewUrl)} className="h-[440px] w-full rounded-control border border-rule bg-white sm:h-[600px]" /><details className="mt-3"><summary className="cursor-pointer text-sm text-navy-700">Text we read from this page</summary><p className="mt-2 whitespace-pre-wrap break-words text-sm text-ink-600">{preview.snippet || 'We could not read any text. Use the preview above.'}</p></details></> : <p className="p-6 text-center text-sm text-ink-500">Choose Preview next to a page to look at it before you give it to someone.</p>}</aside></div></section>
        {duplicates.length > 0 && <section className="rounded-panel border border-amber-200 bg-amber-50 p-5"><h3 className="font-semibold">Some of these people are already in the Data Bank</h3><p className="mt-2 text-sm">Combining adds the chosen pages and fills in empty fields. The recruiter, finished work and deadlines stay as they are.</p><div className="mt-4 space-y-3">{duplicates.map(item => <div key={item.groupId} className="rounded-control bg-white p-4"><p className="font-medium">{item.label} → {item.name} · {item.ref}</p><p className="mt-1 text-sm text-ink-500">{item.phone} · Same {item.matchedOn.join(', ')}</p><a href={`/candidates/${item.candidateId}`} target="_blank" rel="noreferrer" className="mt-2 inline-block text-sm text-navy-700">Open the existing profile ↗</a>{item.canMerge ? <label className="mt-3 flex items-start gap-3 text-sm"><input type="checkbox" disabled={locked} checked={decisions[item.groupId] === item.candidateId} onChange={event => setDecisions(current => { const next = { ...current }; if (event.target.checked) next[item.groupId] = item.candidateId; else delete next[item.groupId]; return next })} />I checked this is the same person. Add their pages to the existing candidate.</label> : <p className="mt-3 text-sm text-stamp-700">You can only combine candidates who are assigned to you. Ask a supervisor to look at who owns this candidate before you go on. This saved review stays private to you.</p>}</div>)}</div></section>}
        <section className="rounded-panel border border-rule bg-field p-5"><h3 className="font-semibold">3. Check and save</h3><p className="mt-2 text-sm text-ink-500">New candidates are given to a recruiter in the usual way and get the initial call deadline. Pages you left out stay in the upload history. Saving the same review twice does not make duplicates.</p><label className="mt-4 flex items-start gap-3 text-sm"><input type="checkbox" className="mt-1 h-4 w-4 shrink-0" disabled={locked || dirty || incomplete} checked={confirmed} onChange={event => setConfirmed(event.target.checked)} /><span>I checked every name and phone number, and every page belongs to the right person.</span></label>{dirty && <p className="mt-3 text-sm text-navy-700">Save the review first, then confirm.</p>}{incomplete && <p className="mt-3 text-sm text-ink-500">Each person needs a name, a phone number and at least one page. Give every page to someone, or say why it is left out.</p>}</section>
      </>}
    </div>
    {closing && <Dialog open onClose={() => setClosing(false)} title="Save your changes?" description="Your files and your last saved review are safe." footer={<><Button onClick={onClose}>Throw away changes</Button><Button variant="primary" disabled={busy || uncertain} onClick={() => void save(true)}>Save and close</Button></>}><p className="text-sm text-ink-600">Save what you changed (people, details and pages) before you close.</p></Dialog>}
  </Dialog>
}
