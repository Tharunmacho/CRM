import { useState } from 'react'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { api, ApiError } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import type { AgencySettings, JobCategory } from '@/shared/model/types'
import { serverFieldErrors } from '@/shared/lib/agency'
import { categoryNameError, cleanCategoryName, sortedCategories, CATEGORY_MAX } from '@/shared/lib/job-categories'
import { EmptyState, Panel } from '@/shared/ui/display'
import { Dialog } from '@/shared/ui/overlay'
import { Button, Field, Input, Switch } from '@/shared/ui/primitives'
import { PanelHead, failureText } from './user-ui'

/** The managed job category list: add, rename, switch off and (only when no job title uses it) delete. */
export function CategoryCard({ editable }: { editable: boolean }) {
  const store = useStore()
  const rows = store.settings.categories ?? []
  const canEdit = editable && store.settings.categoriesEditable === true
  const [editing, setEditing] = useState<JobCategory | 'new' | null>(null)
  const [deleting, setDeleting] = useState<JobCategory | null>(null)
  const [pending, setPending] = useState<string | null>(null)
  const [error, setError] = useState('')

  const afterChange = async (message: string) => {
    try { await store.refresh({ keepCurrent: true }) } catch { /* the live update or the next read catches up */ }
    store.notify(message)
  }
  const toggle = async (item: JobCategory, active: boolean) => {
    if (pending) return
    setPending(item.id); setError('')
    try {
      await api.put<AgencySettings>(`/settings/categories/${item.id}`, { name: item.name, active })
      await afterChange(active ? `${item.name} is offered again` : `${item.name} is no longer offered`)
    } catch (err) { setError(failureText(err)) }
    finally { setPending(null) }
  }

  const sorted = sortedCategories(rows)
  return <Panel flush className="uv-panel as-categories">
    <PanelHead id="as-categories-title" title="Job categories"
      description="The categories you can pick on a job title. Switch one off to stop offering it; job titles that already use it keep it."
      actions={<Button variant="primary" icon={<Plus className="h-4 w-4" />} disabled={!canEdit} onClick={() => { setError(''); setEditing('new') }}>Add category</Button>} />
    {store.settings.categoriesEditable === false && <p role="status" className="uv-warn as-notice as-inline">The job category list is not available yet. The database update that adds it has not been applied, so job titles suggest the categories already in use.</p>}
    {error && <p role="alert" className="uv-error as-inline">{error}</p>}
    {sorted.length === 0
      ? <EmptyState title="No categories yet" description="Add the kinds of work your employers hire for, for example Construction or Hospitality." />
      : <ul className="as-cats" aria-labelledby="as-categories-title">
        {sorted.map((item) => <li key={item.id} className={item.active ? 'as-cat' : 'as-cat is-off'}>
          <span className="as-cat-name">{item.name}</span>
          <label className="as-switch"><Switch compact checked={item.active} disabled={!canEdit || !!pending} label={`${item.name} is offered`} onChange={(on) => void toggle(item, on)} /><span>{item.active ? 'Offered' : 'Not offered'}</span></label>
          <div className="uv-actions">
            <button type="button" className="uv-edit" disabled={!canEdit} title={`Rename ${item.name}`} aria-label={`Rename ${item.name}`} onClick={() => { setError(''); setEditing(item) }}><Pencil size={14} aria-hidden="true" />Rename</button>
            <button type="button" className="uv-edit as-delete" disabled={!canEdit} title={`Delete ${item.name}`} aria-label={`Delete ${item.name}`} onClick={() => { setError(''); setDeleting(item) }}><Trash2 size={14} aria-hidden="true" />Delete</button>
          </div>
        </li>)}
      </ul>}
    {editing && <CategoryDialog key={editing === 'new' ? 'new' : editing.id} item={editing === 'new' ? null : editing} existing={rows}
      onClose={() => setEditing(null)} onSaved={afterChange} />}
    {deleting && <DeleteDialog key={deleting.id} item={deleting} onClose={() => setDeleting(null)} onDeleted={afterChange} />}
  </Panel>
}

function CategoryDialog({ item, existing, onClose, onSaved }: {
  item: JobCategory | null; existing: JobCategory[]; onClose: () => void; onSaved: (message: string) => Promise<void>
}) {
  const [name, setName] = useState(item?.name ?? '')
  const [error, setError] = useState<string | undefined>()
  const [banner, setBanner] = useState('')
  const [busy, setBusy] = useState(false)
  const close = () => { if (!busy) onClose() }
  const save = async () => {
    if (busy) return
    const found = categoryNameError(name, existing, item?.id)
    setError(found)
    if (found) return
    const clean = cleanCategoryName(name)
    if (item && clean === item.name) { onClose(); return }
    setBusy(true); setBanner('')
    try {
      if (item) await api.put<AgencySettings>(`/settings/categories/${item.id}`, { name: clean, active: item.active })
      else await api.post<AgencySettings>('/settings/categories', { name: clean })
      onClose()
      await onSaved(item ? `${clean} saved` : `${clean} added`)
    } catch (err) {
      const fields = err instanceof ApiError ? serverFieldErrors(err.detail) : {}
      if (fields.name) { setError(fields.name); setBanner('') }
      else if (err instanceof ApiError && err.status === 409) { setError(failureText(err)); setBanner('') }
      else setBanner(failureText(err))
    } finally { setBusy(false) }
  }
  return <Dialog open onClose={close} width="md" title={item ? 'Rename category' : 'Add a category'}
    description={item ? 'Job titles that use this category are renamed too.' : 'Use a short, plain name, for example Construction or Hospitality.'}
    footer={<><Button variant="ghost" disabled={busy} onClick={close}>Cancel</Button><Button type="submit" form="category-form" variant="primary" disabled={busy}>{busy ? 'Saving…' : item ? 'Save' : 'Add category'}</Button></>}>
    <form id="category-form" noValidate className="form-stack" onSubmit={(event) => { event.preventDefault(); void save() }}>
      <fieldset disabled={busy} className="form-stack">
        <Field label="Category name" required error={error}><Input autoFocus value={name} maxLength={CATEGORY_MAX} onChange={(e) => { setName(e.target.value); setError(undefined) }} /></Field>
        {banner && <p role="alert" className="uv-error">{banner}</p>}
      </fieldset>
    </form>
  </Dialog>
}

function DeleteDialog({ item, onClose, onDeleted }: { item: JobCategory; onClose: () => void; onDeleted: (message: string) => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const close = () => { if (!busy) onClose() }
  const remove = async () => {
    if (busy) return
    setBusy(true); setError('')
    try {
      await api.del<AgencySettings>(`/settings/categories/${item.id}`)
      onClose()
      await onDeleted(`${item.name} deleted`)
    } catch (err) { setError(failureText(err)) }
    finally { setBusy(false) }
  }
  return <Dialog open onClose={close} width="sm" title={`Delete ${item.name}?`}
    description="You can only delete a category that no job title uses. Otherwise switch it off instead."
    footer={<><Button variant="ghost" disabled={busy} onClick={close}>Cancel</Button><Button variant="danger" disabled={busy} onClick={() => void remove()}>{busy ? 'Deleting…' : 'Delete category'}</Button></>}>
    <p className="uv-note"><strong>{item.name}</strong></p>
    {error && <p role="alert" className="uv-error">{error}</p>}
  </Dialog>
}
