import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { ArrowDown, ArrowUp, Check, Pencil, Plus, X } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { EmptyState, Panel } from '@/shared/ui/display'
import { Button, IconButton, Input, Switch } from '@/shared/ui/primitives'
import { createRemarkTemplate, listAllRemarkTemplates, updateRemarkTemplate } from '@/features/remark-templates/remark-templates-api'
import { TEMPLATE_MAX, applyOrder, moveTemplate, sortTemplates, templateProblem, type RemarkTemplate } from '@/features/remark-templates/remark-templates-model'
import { PanelHead, failureText } from './user-ui'
import { isAdminLevel } from '@/shared/model/role-labels'

/**
 * Users > Agency settings > Remark templates (admins only): the ready-made remarks the Tasks list offers in its Remarks dropdown. Add one,
 * rename it, switch it off (it stops being offered; remarks already written with it stay), and move it up or down (the dropdown follows this order).
 */
export function RemarkTemplatesCard({ editable }: { editable: boolean }) {
  const store = useStore()
  const isAdmin = isAdminLevel(store.me.role)
  const [items, setItems] = useState<RemarkTemplate[] | null>(null)
  const [text, setText] = useState('')
  const [problem, setProblem] = useState('')
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null)
  const [editProblem, setEditProblem] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const canEdit = editable && isAdmin

  const load = useCallback(async () => { setItems(await listAllRemarkTemplates()) }, [])
  useEffect(() => {
    if (!isAdmin) return
    let alive = true
    listAllRemarkTemplates().then((list) => { if (alive) setItems(list) }).catch((failure) => { if (alive) { setItems([]); setError(failureText(failure)) } })
    return () => { alive = false }
  }, [isAdmin])
  if (!isAdmin) return null

  /** One change at a time: run it, tell the person, then show what the server holds. */
  const run = async (change: () => Promise<unknown>, message: string) => {
    if (busy) return false
    setBusy(true); setError(''); setNote('')
    try { await change(); await load(); setNote(message); return true }
    catch (failure) { setError(failureText(failure)); try { await load() } catch { /* the list on screen stays */ } return false }
    finally { setBusy(false) }
  }

  const add = async (event: FormEvent) => {
    event.preventDefault()
    const found = templateProblem(text, items ?? [])
    setProblem(found)
    if (found) return
    if (await run(() => createRemarkTemplate(text.replace(/\s+/g, ' ').trim()), 'Template added.')) setText('')
  }
  const rename = async () => {
    if (!editing) return
    const found = templateProblem(editing.text, items ?? [], editing.id)
    setEditProblem(found)
    if (found) return
    if (await run(() => updateRemarkTemplate(editing.id, { text: editing.text.replace(/\s+/g, ' ').trim() }), 'Template renamed.')) setEditing(null)
  }
  const move = async (id: string, direction: -1 | 1) => {
    const changes = moveTemplate(items ?? [], id, direction)
    if (!changes.length) return
    setItems((current) => (current ? applyOrder(current, changes) : current))
    await run(async () => { for (const change of changes) await updateRemarkTemplate(change.id, { sortOrder: change.sortOrder }) }, 'Order saved.')
  }

  const list = items ? sortTemplates(items) : []
  return <Panel flush className="uv-panel as-categories as-rt">
    <PanelHead id="as-rt-title" title="Remark templates"
      />
    {error && <p role="alert" className="uv-error as-inline">{error}</p>}
    <form className="as-rt-add" noValidate onSubmit={(event) => void add(event)} aria-label="Add a remark template">
      <div className="as-rt-add-field">
        <Input value={text} maxLength={TEMPLATE_MAX} placeholder="New remark, for example Visa in progress" aria-label="New remark template" aria-invalid={problem ? true : undefined}
          disabled={!canEdit || busy} onChange={(event) => { setText(event.target.value); setProblem('') }} />
        {problem && <p role="alert" className="as-rt-problem">{problem}</p>}
      </div>
      <Button type="submit" variant="primary" icon={<Plus className="h-4 w-4" />} disabled={!canEdit || busy}>Add</Button>
    </form>
    <p className="as-rt-note" role="status" aria-live="polite">{note}</p>
    {!items ? <p className="as-inline uv-note">Loading the templates…</p>
      : list.length === 0 ? <EmptyState title="No remark templates yet" />
        : <ul className="as-cats as-rt-list" aria-labelledby="as-rt-title">
          {list.map((item, index) => <li key={item.id} className={item.active ? 'as-cat as-rt-row' : 'as-cat as-rt-row is-off'}>
            {editing?.id === item.id ? (
              <form className="as-rt-edit" noValidate onSubmit={(event) => { event.preventDefault(); void rename() }}>
                <div className="as-rt-edit-field">
                  <Input autoFocus value={editing.text} maxLength={TEMPLATE_MAX} aria-label={`Rename ${item.text}`} aria-invalid={editProblem ? true : undefined} disabled={busy}
                    onChange={(event) => { setEditing({ id: item.id, text: event.target.value }); setEditProblem('') }}
                    onKeyDown={(event) => { if (event.key === 'Escape') { event.preventDefault(); setEditing(null); setEditProblem('') } }} />
                  {editProblem && <p role="alert" className="as-rt-problem">{editProblem}</p>}
                </div>
                <span className="as-rt-edit-actions">
                  <IconButton label="Save the new wording" type="submit" disabled={busy}><Check size={15} aria-hidden="true" /></IconButton>
                  <IconButton label="Cancel the rename" disabled={busy} onClick={() => { setEditing(null); setEditProblem('') }}><X size={15} aria-hidden="true" /></IconButton>
                </span>
              </form>
            ) : <span className="as-cat-name">{item.text}</span>}
            <label className="as-switch"><Switch compact checked={item.active} disabled={!canEdit || busy} label={`${item.text} is offered`}
              onChange={(on) => void run(() => updateRemarkTemplate(item.id, { active: on }), on ? 'Template is offered again.' : 'Template is no longer offered.')} /><span>{item.active ? 'Offered' : 'Not offered'}</span></label>
            <div className="uv-actions as-rt-actions">
              <IconButton label={`Move ${item.text} up`} disabled={!canEdit || busy || index === 0} onClick={() => void move(item.id, -1)}><ArrowUp size={15} aria-hidden="true" /></IconButton>
              <IconButton label={`Move ${item.text} down`} disabled={!canEdit || busy || index === list.length - 1} onClick={() => void move(item.id, 1)}><ArrowDown size={15} aria-hidden="true" /></IconButton>
              <button type="button" className="uv-edit" disabled={!canEdit || busy} title={`Rename ${item.text}`} aria-label={`Rename ${item.text}`}
                onClick={() => { setEditing({ id: item.id, text: item.text }); setEditProblem('') }}><Pencil size={14} aria-hidden="true" />Rename</button>
            </div>
          </li>)}
        </ul>}
  </Panel>
}
