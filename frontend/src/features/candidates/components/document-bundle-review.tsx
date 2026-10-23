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
const kindOptions = [ ['source_bundle', 'Source evidence'], ['resume', 'Resume'], ['passport', 'Passport'], ['aadhaar', 'Aadhaar'], ['photo', 'Photo'], ['education', 'Education'], ['experience', 'Experience'] ].map(([value, label]) => ({ value, label }))
const pageKey = (page: { sourceId: string; pageNumber: number }) => `${page.sourceId}:${page.pageNumber}`
const optionalFields: [string, string, string?][] = [
  ['whatsapp', 'WhatsApp number'], ['dob', 'Date of birth', 'date'], ['gender', 'Gender'], ['nationality', 'Nationality'],
  ['current_city', 'Current city'], ['languages', 'Languages (comma separated)', 'list'], ['passport_issue', 'Passport issue date', 'date'],
  ['passport_expiry', 'Passport expiry date', 'date'], ['passport_place', 'Passport issue place'], ['designations', 'Designations (comma separated)', 'list'],
  ['skills', 'Skills (comma separated)', 'list'], ['total_experience', 'Total experience in years', 'number'], ['gulf_experience', 'Gulf experience in years', 'number'], ['expected_salary', 'Expected salary'],
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
      catch { if (valid()) setNotice('Candidates are saved. Refresh the parent screen to update its list.') }
    }
  }
  const start = async () => {
    if (gate.current) return
    gate.current = true; setBusy(true); setError('')
    try { await finish(await api.post<Bundle>('/document-bundles', { sourceType, sourceId })) }
    catch (err) { if (valid()) setError(err instanceof Error ? err.message : 'The saved review could not be opened.') }
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
      } catch (err) { if (!cancelled && valid()) setError(err instanceof Error ? err.message : 'Progress could not be loaded. Reload the saved review.') }
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
    if (dirty && !window.confirm('Reload the saved review? Unsaved changes on this screen will be replaced.')) return
    gate.current = true; setBusy(true); setError('')
    try { await finish(await api.get<Bundle>(`/document-bundles/${bundle.id}`)) }
    catch (err) { if (valid()) setError(err instanceof Error ? err.message : 'The saved review could not be loaded.') }
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
      apply(value); setNotice('Review saved. You can close this screen and resume it from the same source.')
      if (closeAfter && valid()) onClose()
    } catch (err) {
      if (valid()) { setError(err instanceof Error ? err.message : 'The review could not be saved.'); setUncertain(err instanceof ApiError && (err.status === 0 || err.status === 503 || err.status === 409)) }
    } finally { gate.current = false; if (valid()) setBusy(false) }
  }
  const commit = async () => {
    if (!bundle || gate.current || dirty || !confirmed || uncertain) return
    gate.current = true; setBusy(true); setError('')
    try { await finish(await api.post<Bundle>(`/document-bundles/${bundle.id}/commit`, { revision: bundle.revision, confirmed: true,
      duplicateDecisions: Object.entries(decisions).map(([groupId, candidateId]) => ({ groupId, candidateId })) })) }
    catch (err) {
      if (valid()) {
        setError(err instanceof Error ? err.message : 'Saving could not be confirmed.')
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
  return <Dialog open onClose={close} width="full" title="Review people and pages" description="Separate each person's documents, confirm their details, then save their candidate profiles together."
    footer={<div className="flex w-full flex-wrap items-center justify-between gap-3">
      <span className="text-sm text-ink-500">{dirty ? 'Unsaved changes' : bundle?.status === 'completed' ? 'Candidates saved' : 'Saved progress stays with this source'}</span>
      <div className="flex flex-wrap gap-2"><Button disabled={busy} onClick={close}>{bundle?.status === 'completed' ? 'Done' : 'Close'}</Button>
        {bundle?.status === 'review' && <><Button disabled={locked || !dirty} onClick={() => void save()} icon={<Save size={16} />}>Save review</Button><Button variant="primary" disabled={locked || dirty || incomplete || !confirmed || duplicates.some(item => !decisions[item.groupId])} onClick={() => void commit()}>Confirm and save {people.length} {people.length === 1 ? 'person' : 'people'}</Button></>}
      </div></div>}>
    <div className="mx-auto max-w-[1440px] space-y-6">
      {error && <div role="alert" className="rounded-panel bg-stamp-50 p-4 text-sm text-stamp-700">{error}<div className="mt-3 flex flex-wrap gap-2"><Button disabled={busy} onClick={() => void (bundle ? reload() : start())} icon={<RefreshCw size={16} />}>{bundle ? 'Reload saved review' : 'Open saved review'}</Button></div>{uncertain && <p className="mt-2">A save may have succeeded or another reviewer may have changed this source. Reload this same review before retrying.</p>}</div>}
      {notice && <p role="status" className="rounded-panel bg-navy-50 p-4 text-sm text-navy-700">{notice}</p>}
      {!bundle && !error && <p role="status">Opening your saved review…</p>}
      {bundle?.status === 'pending' && <section className="rounded-panel border border-rule bg-field p-6"><FileText className="mb-3 text-navy-600" /><h3 className="font-semibold">{bundle.lastError ? 'Page preparation needs attention' : 'Preparing page previews and possible people'}</h3><p className="mt-2 text-sm text-ink-500">{bundle.lastError || 'The document worker is checking the saved originals. You can close this screen and return; preparation continues.'}</p>{bundle.lastError && <Button className="mt-4" disabled={busy} onClick={() => void start()}>Retry page preparation</Button>}</section>}
      {bundle?.status === 'completed' && <section className="rounded-panel border border-emerald-200 bg-emerald-50 p-6"><CheckCircle2 className="mb-3 text-emerald-600" /><h3 className="text-lg font-semibold">{bundle.candidateIds.length} candidate profiles saved</h3><p className="mt-2 text-sm">Only each person's selected pages were attached. Existing candidate ownership and deadlines were retained.</p><div className="mt-4 grid gap-3 sm:grid-cols-2">{bundle.groups.map(person => <Link onClick={onClose} key={person.id} className="rounded-control border border-emerald-200 bg-white p-4 font-medium text-navy-700" to={`/candidates/${person.candidateId}`}>{person.label} <span className="block text-sm font-normal">Open candidate profile →</span></Link>)}</div></section>}
      {bundle?.status === 'completed' && <section className="rounded-panel border border-rule p-5"><h3 className="font-semibold">Original source uploads</h3><p className="mt-2 text-sm text-ink-500">These are the unchanged full uploads and may contain several people. Each candidate profile has only their selected pages.</p><ul className="mt-4 space-y-3">{bundle.sources.filter(source => source.originalUrl).map(source => <li key={source.id}><a className="inline-flex max-w-full items-start gap-2 text-sm font-medium text-navy-700" href={fileHref(`${source.originalUrl}?download=true`)}><FileText size={16} className="mt-0.5 shrink-0" /><span className="break-words">Download original: {source.name}</span></a></li>)}</ul></section>}
      {bundle?.status === 'review' && <>
        <section className="rounded-panel border border-rule bg-field p-5"><div className="flex flex-wrap items-start justify-between gap-4"><div><h3 className="font-semibold">1. Review the proposed people</h3><p className="mt-2 max-w-3xl text-sm text-ink-500">Names and contacts are proposed from explicit labels in readable pages. Check every preview. Unclear pages stay unassigned; page order alone does not prove ownership.</p></div><Button disabled={locked} onClick={() => { setPeople(current => [...current, { key: crypto.randomUUID(), label: `Person ${current.length + 1}`, fields: {}, pages: [] }]); changed() }} icon={<Plus size={16} />}>Add person</Button></div><div className="mt-4 flex flex-wrap gap-3 text-sm"><span className="rounded-control bg-white px-3 py-2">{people.length} people</span><span className="rounded-control bg-white px-3 py-2">{bundle.pages.length} source pages</span><span className="rounded-control bg-white px-3 py-2">{remaining} unassigned</span><span className="rounded-control bg-white px-3 py-2">{ignored.length} excluded</span></div></section>
        <div className="grid gap-5 lg:grid-cols-2">{people.map((person, index) => <section key={person.key} className="min-w-0 rounded-panel border border-rule p-5"><div className="mb-4 flex items-start justify-between gap-3"><div className="flex min-w-0 items-center gap-3"><Users size={20} className="shrink-0 text-navy-600" /><h3 className="break-words font-semibold">Person {index + 1} · {person.label}</h3></div><Button size="sm" variant="dangerGhost" disabled={locked} onClick={() => { setPeople(current => current.filter(item => item.key !== person.key)); changed() }}>Remove</Button></div><p className="mb-4 text-sm text-ink-500">{person.pages.length} pages assigned. Removing a person returns their pages to unassigned.</p><div className="grid gap-4 sm:grid-cols-2">{[['name', 'Full name *'], ['phone', 'Phone *'], ['email', 'Email'], ['passport_no', 'Passport number']].map(([key, label]) => <Field key={key} label={label}><Input disabled={locked} value={String(person.fields[key] || '')} onChange={event => field(person.key, key, event.target.value)} /></Field>)}</div>{sourceType === 'manual' && <Field label="Candidate source" className="mt-4"><Select disabled={locked} value={String(person.fields.source || 'walk_in')} onChange={event => field(person.key, 'source', event.target.value)}><option value="walk_in">Walk-in</option><option value="referral">Referral</option><option value="other">Other</option><option value="email">Email</option><option value="whatsapp">WhatsApp</option></Select></Field>}<details className="mt-4"><summary className="cursor-pointer text-sm font-medium text-navy-700">Additional candidate details</summary><div className="mt-4 grid gap-4 sm:grid-cols-2">{optionalFields.map(([key, label, type]) => <Field key={key} label={label}><Input disabled={locked} type={type === 'date' || type === 'number' ? type : 'text'} min={type === 'number' ? 0 : undefined} max={type === 'number' ? 80 : undefined} value={Array.isArray(person.fields[key]) ? (person.fields[key] as string[]).join(', ') : String(person.fields[key] ?? '')} onChange={event => field(person.key, key, type === 'list' ? event.target.value.split(',').map(value => value.trim()).filter(Boolean) : type === 'number' && event.target.value !== '' ? Number(event.target.value) : event.target.value)} /></Field>)}</div></details></section>)}</div>
        <section><h3 className="mb-2 font-semibold">2. Assign every page to one person</h3><p className="mb-4 text-sm text-ink-500">Choose the person and document type, or exclude a page with a reason. A page containing multiple people must be excluded and uploaded again as separate pages.</p><div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]"><div className="space-y-3">{bundle.pages.map(page => { const key = pageKey(page); const owner = assigned.get(key); const excluded = exclusions.get(key); const selection = owner?.pages.find(item => pageKey(item) === key); return <article key={key} className="rounded-panel border border-rule p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0 flex-1"><h4 className="break-words font-medium">{bundle.sources.find(source => source.id === page.sourceId)?.name} · Page {page.pageNumber}</h4><p className="mt-1 text-sm text-ink-500">{page.blocked ? 'Multiple people on one page — cannot attach' : page.status === 'read' ? 'Readable page — confirm ownership' : 'No reliable reading — inspect the preview manually'}</p></div><Button size="sm" onClick={() => setPreview(page)}>Preview</Button></div>{page.reason && <p className="mt-2 text-sm text-ink-500">{page.reason}</p>}<div className="mt-4 grid gap-3 sm:grid-cols-2"><Field label="Page belongs to"><Select disabled={locked} value={owner?.key || (excluded ? 'exclude' : '')} onChange={event => assign(page, event.target.value)}><option value="">Unassigned - choose person</option>{!page.blocked && people.map((person, index) => <option key={person.key} value={person.key}>Person {index + 1}: {person.label}</option>)}<option value="exclude">Exclude this page</option></Select></Field>{owner && <Field label="Document type"><Select disabled={locked} value={selection?.kind || 'source_bundle'} onChange={event => { const kind = event.target.value; setPeople(current => current.map(person => ({ ...person, pages: person.pages.map(item => pageKey(item) === key ? { ...item, kind } : item) }))); changed() }}>{kindOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</Select></Field>}</div>{excluded && <Field label="Why is this page excluded?" className="mt-3"><Textarea disabled={locked} value={excluded.reason} maxLength={500} onChange={event => { setIgnored(current => current.map(item => pageKey(item) === key ? { ...item, reason: event.target.value } : item)); changed() }} placeholder="For example, agency cover sheet or mixed-person page to upload separately" /></Field>}</article> })}</div><aside className="min-w-0 rounded-panel border border-rule bg-field p-4 xl:sticky xl:top-0">{preview ? <><div className="mb-3 flex flex-wrap items-center justify-between gap-3"><h4 className="font-medium">Page {preview.pageNumber} preview</h4><a className="inline-flex items-center gap-2 text-sm text-navy-700" href={fileHref(preview.previewUrl)} target="_blank" rel="noreferrer">Open full preview <ExternalLink size={14} /></a></div><iframe title={`Original page ${preview.pageNumber}`} src={fileHref(preview.previewUrl)} className="h-[440px] w-full rounded-control border border-rule bg-white sm:h-[600px]" /><details className="mt-3"><summary className="cursor-pointer text-sm text-navy-700">Locally read text excerpt</summary><p className="mt-2 whitespace-pre-wrap break-words text-sm text-ink-600">{preview.snippet || 'No readable text was extracted. Use the original preview.'}</p></details></> : <p className="p-6 text-center text-sm text-ink-500">Select Preview beside a page to check the original before assigning it.</p>}</aside></div></section>
        {duplicates.length > 0 && <section className="rounded-panel border border-amber-200 bg-amber-50 p-5"><h3 className="font-semibold">Existing candidates need your decision</h3><p className="mt-2 text-sm">Merging adds selected document evidence and fills empty fields. Existing owner, completed work and deadlines stay unchanged.</p><div className="mt-4 space-y-3">{duplicates.map(item => <div key={item.groupId} className="rounded-control bg-white p-4"><p className="font-medium">{item.label} → {item.name} · {item.ref}</p><p className="mt-1 text-sm text-ink-500">{item.phone} · Matched on {item.matchedOn.join(', ')}</p><a href={`/candidates/${item.candidateId}`} target="_blank" rel="noreferrer" className="mt-2 inline-block text-sm text-navy-700">Review existing profile ↗</a>{item.canMerge ? <label className="mt-3 flex items-start gap-3 text-sm"><input type="checkbox" disabled={locked} checked={decisions[item.groupId] === item.candidateId} onChange={event => setDecisions(current => { const next = { ...current }; if (event.target.checked) next[item.groupId] = item.candidateId; else delete next[item.groupId]; return next })} />I checked this identity. Add this person's pages to the existing candidate.</label> : <p className="mt-3 text-sm text-stamp-700">You can merge only candidates assigned to you. Ask a supervisor to review the existing candidate ownership before continuing. This saved manual review remains private to you.</p>}</div>)}</div></section>}
        <section className="rounded-panel border border-rule bg-field p-5"><h3 className="font-semibold">3. Confirm details and save</h3><p className="mt-2 text-sm text-ink-500">New candidates follow the current assignment rules and initial-call deadline. Excluded pages remain in source history. Saving the same completed review again returns the same candidates.</p><label className="mt-4 flex items-start gap-3 text-sm"><input type="checkbox" className="mt-1 h-4 w-4 shrink-0" disabled={locked || dirty || incomplete} checked={confirmed} onChange={event => setConfirmed(event.target.checked)} /><span>I checked every person's name and phone and verified that all assigned pages belong to that person.</span></label>{dirty && <p className="mt-3 text-sm text-navy-700">Save the review first, then confirm the saved page assignments.</p>}{incomplete && <p className="mt-3 text-sm text-ink-500">Each person needs a name, phone and at least one page. Assign every remaining page or record its exclusion reason.</p>}</section>
      </>}
    </div>
    {closing && <Dialog open onClose={() => setClosing(false)} title="Keep your review changes?" description="Your original files and last saved review are already retained." footer={<><Button onClick={onClose}>Discard unsaved changes</Button><Button variant="primary" disabled={busy || uncertain} onClick={() => void save(true)}>Save and close</Button></>}><p className="text-sm text-ink-600">Save the changes you made to people, details and page assignments before closing.</p></Dialog>}
  </Dialog>
}
