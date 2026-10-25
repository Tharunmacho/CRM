import { useState } from 'react'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { api, ApiError } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import type { AgencySettings, CurrencyOption } from '@/shared/model/types'
import { currencyFormErrors, serverFieldErrors, type CurrencyForm } from '@/shared/lib/agency'
import { Badge, EmptyState, Panel } from '@/shared/ui/display'
import { DataTable, type Column } from '@/shared/ui/table'
import { Dialog } from '@/shared/ui/overlay'
import { Button, Field, Input, Switch } from '@/shared/ui/primitives'
import { PanelHead, failureText } from './user-ui'

const BLANK: CurrencyForm = { code: '', name: '', symbol: '' }

/** The managed currency list: add, rename, switch off and (only when never used) delete. */
export function CurrencyCard({ editable, defaultOrder, defaultAdvance }: { editable: boolean; defaultOrder: string; defaultAdvance: string }) {
  const store = useStore()
  const rows = store.settings.currencies
  const [editing, setEditing] = useState<CurrencyOption | 'new' | null>(null)
  const [deleting, setDeleting] = useState<CurrencyOption | null>(null)
  const [pending, setPending] = useState<string | null>(null)
  const [error, setError] = useState('')

  const afterChange = async (message: string) => {
    try { await store.refresh({ keepCurrent: true }) } catch { /* the live update or the next read catches up */ }
    store.notify(message)
  }
  const toggle = async (item: CurrencyOption, active: boolean) => {
    if (pending) return
    setPending(item.code); setError('')
    try {
      await api.put<AgencySettings>(`/settings/currencies/${item.code}`, { name: item.name, symbol: item.symbol ?? '', active })
      await afterChange(active ? `${item.code} is offered again` : `${item.code} is no longer offered`)
    } catch (err) { setError(failureText(err)) }
    finally { setPending(null) }
  }

  const sorted = [...rows].sort((x, y) => x.sortOrder - y.sortOrder || x.code.localeCompare(y.code))
  const columns: Column<CurrencyOption>[] = [
    { key: 'code', header: 'Code', width: '9%', render: (c) => <strong className="as-code">{c.code}</strong> },
    { key: 'name', header: 'Name', width: '22%', render: (c) => <span className="as-name">{c.name}</span> },
    { key: 'symbol', header: 'Symbol', width: '9%', render: (c) => c.symbol ? <b className="as-symbol">{c.symbol}</b> : <span className="uv-none">None</span> },
    {
      key: 'offered', header: 'Offered', width: '16%',
      render: (c) => <label className="as-switch"><Switch compact checked={c.active} disabled={!editable || !!pending} label={`${c.code} is offered`} onChange={(on) => void toggle(c, on)} /><span>{c.active ? 'Offered' : 'Not offered'}</span></label>,
    },
    {
      key: 'defaults', header: 'Default for', width: '16%',
      render: (c) => c.code === defaultOrder || c.code === defaultAdvance ? <div className="as-tags">
        {c.code === defaultOrder && <Badge tone="navy" variant="chip">Orders</Badge>}
        {c.code === defaultAdvance && <Badge tone="navy" variant="chip">Advances</Badge>}
      </div> : <span className="uv-none">None</span>,
    },
    {
      key: 'actions', header: 'Actions', actions: true,
      render: (c) => <div className="uv-actions">
        <button type="button" className="uv-edit" disabled={!editable} title={`Edit ${c.code}`} aria-label={`Edit ${c.code}`} onClick={() => { setError(''); setEditing(c) }}><Pencil size={14} aria-hidden="true" />Edit</button>
        <button type="button" className="uv-edit as-delete" disabled={!editable} title={`Delete ${c.code}`} aria-label={`Delete ${c.code}`} onClick={() => { setError(''); setDeleting(c) }}><Trash2 size={14} aria-hidden="true" />Delete</button>
      </div>,
    },
  ]
  return <Panel flush className="uv-panel as-currencies">
    <PanelHead id="as-currencies-title" title="Currencies"
      description="The currencies offered on orders and for the advance payment. Switch one off to stop offering it; orders and payments that already use it keep it."
      actions={<Button variant="primary" icon={<Plus className="h-4 w-4" />} disabled={!editable} onClick={() => { setError(''); setEditing('new') }}>Add currency</Button>} />
    {error && <p role="alert" className="uv-error as-inline">{error}</p>}
    <DataTable compact pageSize={50} rows={sorted} columns={columns} rowKey={(c) => c.code} rowClassName={(c) => c.active ? undefined : 'uv-row-off'}
      empty={<EmptyState title="No currencies yet" description="Add the currencies your orders are priced in." />} />
    {editing && <CurrencyDialog key={editing === 'new' ? 'new' : editing.code} item={editing === 'new' ? null : editing} existing={rows}
      onClose={() => setEditing(null)} onSaved={afterChange} />}
    {deleting && <DeleteDialog key={deleting.code} item={deleting} onClose={() => setDeleting(null)} onDeleted={afterChange} />}
  </Panel>
}

function CurrencyDialog({ item, existing, onClose, onSaved }: {
  item: CurrencyOption | null; existing: CurrencyOption[]; onClose: () => void; onSaved: (message: string) => Promise<void>
}) {
  const [form, setForm] = useState<CurrencyForm>(item ? { code: item.code, name: item.name, symbol: item.symbol ?? '' } : BLANK)
  const [errors, setErrors] = useState<Partial<Record<keyof CurrencyForm, string>>>({})
  const [banner, setBanner] = useState('')
  const [busy, setBusy] = useState(false)
  const set = (key: keyof CurrencyForm, value: string) => { setForm((c) => ({ ...c, [key]: value })); setErrors((c) => ({ ...c, [key]: undefined })) }
  const close = () => { if (!busy) onClose() }
  const save = async () => {
    if (busy) return
    const found = currencyFormErrors(form, existing, !item)
    setErrors(found)
    if (Object.keys(found).length) return
    setBusy(true); setBanner('')
    const body = { name: form.name.trim(), symbol: form.symbol.trim() }
    try {
      if (item) await api.put<AgencySettings>(`/settings/currencies/${item.code}`, { ...body, active: item.active })
      else await api.post<AgencySettings>('/settings/currencies', { ...body, code: form.code.trim().toUpperCase() })
      onClose()
      await onSaved(item ? `${item.code} saved` : `${form.code.trim().toUpperCase()} added`)
    } catch (err) {
      const fields = err instanceof ApiError ? serverFieldErrors(err.detail) : {}
      setErrors(fields)
      setBanner(Object.keys(fields).length ? '' : failureText(err))
    } finally { setBusy(false) }
  }
  return <Dialog open onClose={close} width="md" title={item ? `Edit ${item.code}` : 'Add a currency'}
    description={item ? 'The code stays the same so records that use it keep working.' : 'Use the 3-letter code, for example AED.'}
    footer={<><Button variant="ghost" disabled={busy} onClick={close}>Cancel</Button><Button type="submit" form="currency-form" variant="primary" disabled={busy}>{busy ? 'Saving…' : item ? 'Save' : 'Add currency'}</Button></>}>
    <form id="currency-form" noValidate className="form-stack" onSubmit={(event) => { event.preventDefault(); void save() }}>
      <fieldset disabled={busy} className="form-stack">
        <Field label="Code" required error={errors.code}><Input autoFocus value={form.code} disabled={!!item} maxLength={3} autoCapitalize="characters" onChange={(e) => set('code', e.target.value.toUpperCase())} /></Field>
        <Field label="Name" required error={errors.name}><Input value={form.name} maxLength={80} onChange={(e) => set('name', e.target.value)} /></Field>
        <Field label="Symbol" error={errors.symbol} hint="Optional, for example a currency sign."><Input value={form.symbol} maxLength={8} onChange={(e) => set('symbol', e.target.value)} /></Field>
        {banner && <p role="alert" className="uv-error">{banner}</p>}
      </fieldset>
    </form>
  </Dialog>
}

function DeleteDialog({ item, onClose, onDeleted }: { item: CurrencyOption; onClose: () => void; onDeleted: (message: string) => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const close = () => { if (!busy) onClose() }
  const remove = async () => {
    if (busy) return
    setBusy(true); setError('')
    try {
      await api.del<AgencySettings>(`/settings/currencies/${item.code}`)
      onClose()
      await onDeleted(`${item.code} deleted`)
    } catch (err) { setError(failureText(err)) }
    finally { setBusy(false) }
  }
  return <Dialog open onClose={close} width="sm" title={`Delete ${item.code}?`}
    description="You can only delete a currency that no order or payment has ever used. Otherwise switch it off instead."
    footer={<><Button variant="ghost" disabled={busy} onClick={close}>Cancel</Button><Button variant="danger" disabled={busy} onClick={() => void remove()}>{busy ? 'Deleting…' : 'Delete currency'}</Button></>}>
    <p className="uv-note"><strong>{item.code}</strong> · {item.name}</p>
    {error && <p role="alert" className="uv-error as-inline">{error}</p>}
  </Dialog>
}
