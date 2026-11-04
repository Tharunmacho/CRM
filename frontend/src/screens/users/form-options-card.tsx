import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowDown, ArrowUp, EyeOff, Pencil, Plus, Trash2 } from 'lucide-react'
import { api, ApiError } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { EmptyState, Panel } from '@/shared/ui/display'
import { Dialog } from '@/shared/ui/overlay'
import { Button, Field, Input, Select, Switch } from '@/shared/ui/primitives'
import { normalizeFormOptions, optionLabelError, reorder, slugValue, toneStyle, TONE_CHOICES, type CategoryMetas, type FormOption, type FormOptions } from '@/features/form-options/form-options-model'
import { refreshFormOptions } from '@/features/form-options/use-form-options'
import { PanelHead, failureText } from './user-ui'

/** Categories whose choices carry a colour (the interest cards and the service-charges badge). */
const COLOURED = new Set(['interest_level', 'service_charges_status'])

/**
 * Users > Agency settings > Form options (admins only): the lists behind the candidate form's dropdowns and cards.
 * Add, rename, reorder, hide and, for the interest levels, choose the colour. Every open form follows live.
 */
export function FormOptionsCard({ editable }: { editable: boolean }) {
  const store = useStore()
  const isAdmin = store.me.role === 'admin'
  const [data, setData] = useState<{ options: FormOptions; meta: CategoryMetas; order: string[] } | null>(null)
  const [category, setCategory] = useState('interest_level')
  const [error, setError] = useState('')
  const [pending, setPending] = useState<string | null>(null)
  const [editing, setEditing] = useState<FormOption | 'new' | null>(null)
  const [removing, setRemoving] = useState<FormOption | null>(null)

  const load = useCallback(async () => {
    try {
      const body = await api.get<{ categories?: { key: string }[] }>('/form-options?includeInactive=true')
      const parsed = normalizeFormOptions(body)
      setData({ ...parsed, order: (body.categories ?? []).map((item) => item.key) })
      setError('')
    } catch (err) { setError(failureText(err)) }
  }, [])
  useEffect(() => { if (isAdmin) void load() }, [isAdmin, load])
  if (!isAdmin) return null

  /** After any change: reload this list and tell every open form to reload its own copy. */
  const changed = async (message?: string) => {
    await load()
    void refreshFormOptions()
    if (message) store.notify(message)
  }
  const rows = data ? [...(data.options[category] ?? [])].sort((a, b) => a.sort - b.sort || a.label.localeCompare(b.label)) : []
  const meta = data?.meta[category]
  const canEdit = editable && Boolean(data)
  const coloured = COLOURED.has(category)

  const run = async (id: string, work: () => Promise<unknown>, message?: string) => {
    if (pending) return
    setPending(id); setError('')
    try { await work(); await changed(message) } catch (err) { setError(failureText(err)) } finally { setPending(null) }
  }
  const move = (row: FormOption, direction: -1 | 1) => {
    const next = reorder(rows, row.value, direction)
    return run(row.id ?? row.value, () => api.post('/form-options/reorder', { category, ids: next.map((item) => item.id).filter(Boolean) }))
  }

  return <Panel flush className="uv-panel as-categories as-options">
    <PanelHead id="as-options-title" title="Form options"
      description="The choices people pick from on the candidate form: interest levels, service charges, marital status, job duration, best time to contact, source and more. Changes show on every open form straight away."
      actions={<Button variant="primary" icon={<Plus className="h-4 w-4" />} disabled={!canEdit || !meta || meta.fixedValues} onClick={() => { setError(''); setEditing('new') }}>Add option</Button>} />
    {data && <div className="as-opt-tabs" role="tablist" aria-label="Lists">
      {data.order.map((key) => <button key={key} type="button" role="tab" aria-selected={key === category} className={key === category ? 'is-on' : ''} onClick={() => { setCategory(key); setError('') }}>{data.meta[key]?.label ?? key}</button>)}
    </div>}
    {meta?.fixedValues && <p role="status" className="uv-warn as-notice as-inline">The values of this list are fixed by the system. You can rename them, change their order or hide one, but not add new ones.</p>}
    {meta?.strict && !meta.fixedValues && <p role="status" className="as-notice as-inline as-opt-note">New candidates can only pick from this list. A hidden option stays on the candidates who already have it.</p>}
    {meta && meta.allowCustom && <p role="status" className="as-notice as-inline as-opt-note">This list is a set of suggestions. Documents and people can still enter their own wording.</p>}
    {error && <p role="alert" className="uv-error as-inline">{error}</p>}
    {!data && !error && <p className="as-inline uv-note">Loading the lists…</p>}
    {data && rows.length === 0 && <EmptyState title="Nothing in this list yet" description="Add the first option." />}
    {rows.length > 0 && <ul className="as-cats" aria-labelledby="as-options-title">
      {rows.map((row, index) => {
        const tone = toneStyle(row.tone)
        const busy = pending === (row.id ?? row.value)
        return <li key={row.id ?? row.value} className={row.active ? 'as-cat as-opt' : 'as-cat as-opt is-off'}>
          <span className="as-opt-main">
            {coloured && <i className="as-opt-dot" data-tone={tone.name} aria-hidden />}
            <span><span className="as-cat-name">{row.label}</span>{row.hint && <small className="as-opt-hint">{row.hint}</small>}</span>
          </span>
          <label className="as-switch"><Switch compact checked={row.active} disabled={!canEdit || busy || Boolean(pending)} label={`${row.label} is shown`}
            onChange={(on) => void run(row.id ?? row.value, () => api.put(`/form-options/${row.id}`, { isActive: on }), on ? `${row.label} is shown again` : `${row.label} is hidden`)} /><span>{row.active ? 'Shown' : 'Hidden'}</span></label>
          <div className="uv-actions">
            <button type="button" className="uv-edit" disabled={!canEdit || Boolean(pending) || index === 0} title="Move up" aria-label={`Move ${row.label} up`} onClick={() => void move(row, -1)}><ArrowUp size={14} aria-hidden="true" /></button>
            <button type="button" className="uv-edit" disabled={!canEdit || Boolean(pending) || index === rows.length - 1} title="Move down" aria-label={`Move ${row.label} down`} onClick={() => void move(row, 1)}><ArrowDown size={14} aria-hidden="true" /></button>
            <button type="button" className="uv-edit" disabled={!canEdit || Boolean(pending)} title={`Edit ${row.label}`} aria-label={`Edit ${row.label}`} onClick={() => { setError(''); setEditing(row) }}><Pencil size={14} aria-hidden="true" />Edit</button>
            <button type="button" className="uv-edit as-delete" disabled={!canEdit || Boolean(pending)} title={row.system ? `Hide ${row.label}` : `Delete ${row.label}`} aria-label={`${row.system ? 'Hide' : 'Delete'} ${row.label}`} onClick={() => { setError(''); setRemoving(row) }}>
              {row.system ? <EyeOff size={14} aria-hidden="true" /> : <Trash2 size={14} aria-hidden="true" />}{row.system ? 'Hide' : 'Delete'}</button>
          </div>
        </li>
      })}
    </ul>}
    {editing && meta && <OptionDialog key={editing === 'new' ? 'new' : editing.id} category={category} meta={meta} item={editing === 'new' ? null : editing} rows={rows} coloured={coloured}
      onClose={() => setEditing(null)} onSaved={changed} />}
    {removing && <RemoveDialog key={removing.id} item={removing} onClose={() => setRemoving(null)} onDone={changed} />}
  </Panel>
}

function OptionDialog({ category, meta, item, rows, coloured, onClose, onSaved }: {
  category: string; meta: CategoryMetas[string]; item: FormOption | null; rows: FormOption[]; coloured: boolean
  onClose: () => void; onSaved: (message: string) => Promise<void>
}) {
  const [label, setLabel] = useState(item?.label ?? '')
  const [hint, setHint] = useState(item?.hint ?? '')
  const [tone, setTone] = useState(item?.tone && TONE_CHOICES.some((choice) => choice.value === item.tone) ? item.tone : '')
  const [error, setError] = useState<string | undefined>()
  const [banner, setBanner] = useState('')
  const [busy, setBusy] = useState(false)
  const close = () => { if (!busy) onClose() }
  // What gets stored: free-text lists keep the wording itself ("2 years"), the strict lists a plain code ("somewhat_keen").
  const value = useMemo(() => (item ? item.value : meta.strict ? slugValue(label) : label.trim().replace(/\s+/g, ' ')), [item, meta.strict, label])
  const save = async () => {
    if (busy) return
    const found = optionLabelError(label, rows, item?.value) ?? (!item && !value ? 'Use letters or numbers in the name.' : undefined)
    setError(found)
    if (found) return
    setBusy(true); setBanner('')
    try {
      const clean = label.trim().replace(/\s+/g, ' ')
      if (item) await api.put(`/form-options/${item.id}`, { label: clean, hint: hint.trim() || null, tone: tone || null })
      else await api.post('/form-options', { category, value, label: clean, hint: hint.trim() || null, tone: tone || null })
      onClose()
      await onSaved(item ? `${clean} saved` : `${clean} added`)
    } catch (err) {
      if (err instanceof ApiError && (err.status === 409 || err.status === 422)) setError(failureText(err))
      else setBanner(failureText(err))
    } finally { setBusy(false) }
  }
  return <Dialog open onClose={close} width="md" title={item ? 'Edit option' : `Add to ${meta.label}`}
    description={item ? 'Candidates who already have this option keep it; only the name shown changes.' : 'Use a short, plain name.'}
    footer={<><Button variant="ghost" disabled={busy} onClick={close}>Cancel</Button><Button type="submit" form="form-option-form" variant="primary" disabled={busy}>{busy ? 'Saving…' : item ? 'Save' : 'Add option'}</Button></>}>
    <form id="form-option-form" noValidate className="form-stack" onSubmit={(event) => { event.preventDefault(); void save() }}>
      <fieldset disabled={busy} className="form-stack">
        <Field label="Name" required error={error} hint={!item && value ? `Stored as: ${value}` : item ? `Stored as: ${item.value} (never changes)` : undefined}>
          <Input autoFocus value={label} maxLength={80} onChange={(e) => { setLabel(e.target.value); setError(undefined) }} />
        </Field>
        <Field label="Short description" hint="Shown under the name on cards, for example the interest levels.">
          <Input value={hint} maxLength={255} onChange={(e) => setHint(e.target.value)} />
        </Field>
        {coloured && <Field label="Colour" hint="The colour of the card or badge.">
          <Select value={tone} onChange={(e) => setTone(e.target.value)}>
            <option value="">No colour</option>
            {TONE_CHOICES.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
          </Select>
        </Field>}
        {banner && <p role="alert" className="uv-error">{banner}</p>}
      </fieldset>
    </form>
  </Dialog>
}

function RemoveDialog({ item, onClose, onDone }: { item: FormOption; onClose: () => void; onDone: (message: string) => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const close = () => { if (!busy) onClose() }
  const remove = async () => {
    if (busy) return
    setBusy(true); setError('')
    try {
      const result = await api.del<{ deleted: boolean; deactivated: boolean; inUse: number }>(`/form-options/${item.id}`)
      onClose()
      await onDone(result.deleted ? `${item.label} deleted` : `${item.label} is hidden${result.inUse ? ` (${result.inUse} ${result.inUse === 1 ? 'candidate uses' : 'candidates use'} it)` : ''}`)
    } catch (err) { setError(failureText(err)) } finally { setBusy(false) }
  }
  return <Dialog open onClose={close} width="sm" title={`${item.system ? 'Hide' : 'Delete'} ${item.label}?`}
    description={item.system ? 'This option came with the system, so it is hidden rather than deleted. You can show it again at any time.' : 'An option that candidates already use is hidden instead of deleted, so nothing they have is lost.'}
    footer={<><Button variant="ghost" disabled={busy} onClick={close}>Cancel</Button><Button variant="danger" disabled={busy} onClick={() => void remove()}>{busy ? 'Working…' : item.system ? 'Hide option' : 'Delete option'}</Button></>}>
    <p className="uv-note"><strong>{item.label}</strong></p>
    {error && <p role="alert" className="uv-error">{error}</p>}
  </Dialog>
}
