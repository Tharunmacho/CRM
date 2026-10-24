import { useMemo, useState, type ReactNode } from 'react'
import { format } from 'date-fns'
import { CalendarClock, Link2, ListChecks } from 'lucide-react'
import { api } from '@/shared/api/client'
import { cn } from '@/shared/lib/cn'
import { useStore } from '@/shared/state/crm-store'
import type { Priority, TodoItem } from '@/shared/model/types'
import { Button, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { DatePicker } from '@/shared/ui/date-picker'
import { LINK_KINDS, PRIORITIES, linkKindOf, toInputValue, type LinkKind } from './todo-meta'

function Section({ icon, title, hint, children }: { icon: ReactNode; title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="form-section">
      <header>
        <span aria-hidden="true">{icon}</span>
        <div><h3>{title}</h3>{hint && <p>{hint}</p>}</div>
      </header>
      <div className="td-form-grid">{children}</div>
    </section>
  )
}

/** Add a to-do, or edit one. `initialTitle` carries over what was typed in the quick-add row. */
export function TodoDialog({ onClose, item, initialTitle = '' }: { onClose: () => void; item?: TodoItem | null; initialTitle?: string }) {
  const store = useStore()
  const defaultDue = (() => {
    const d = new Date()
    d.setHours(d.getHours() + 2, 0, 0, 0)
    return format(d, "yyyy-MM-dd'T'HH:mm")
  })()

  const initialKind = linkKindOf(item?.linkTo)
  const [form, setForm] = useState({
    title: item?.title || initialTitle,
    notes: item?.notes || '',
    dueAt: item ? toInputValue(item.dueAt) : defaultDue,
    priority: (item?.priority || 'normal') as Priority,
    kind: (initialKind || '') as LinkKind | '',
    link: item?.linkTo || '',
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [titleError, setTitleError] = useState('')
  const set = <K extends keyof typeof form>(k: K, v: (typeof form)[K]) => setForm((f) => ({ ...f, [k]: v }))

  const recordOptions = useMemo(() => {
    const designations = new Map(store.designations.map((d) => [d.id, d.title]))
    const companies = new Map(store.companies.map((c) => [c.id, c.name]))
    const options: Record<LinkKind, { value: string; label: string; short: string }[]> = {
      client: store.clients.map((c) => ({ value: `/clients/${c.id}`, label: c.name, short: c.name })),
      order: store.orders.map((o) => ({
        value: `/orders/${o.id}`,
        label: [o.ref, designations.get(o.designationId), companies.get(o.companyId)].filter(Boolean).join(' · '),
        short: o.ref,
      })),
      candidate: store.candidates.map((c) => ({ value: `/candidates/${c.id}`, label: `${c.name} · ${c.ref}`, short: c.name })),
    }
    // Keep a saved link selectable even when its record is not in the loaded workspace.
    if (item?.linkTo && initialKind && !options[initialKind].some((o) => o.value === item.linkTo)) {
      options[initialKind].unshift({ value: item.linkTo, label: item.linkLabel || 'Linked item', short: item.linkLabel || 'Linked item' })
    }
    return options
  }, [store.clients, store.orders, store.candidates, store.designations, store.companies, item, initialKind])

  const chosen = form.kind ? recordOptions[form.kind].find((o) => o.value === form.link) : undefined

  const save = async () => {
    if (busy) return
    if (!form.title.trim()) { setTitleError('Enter a title for this to-do.'); return }
    const due = new Date(form.dueAt)
    if (!form.dueAt || !Number.isFinite(due.getTime())) { setError('Choose a valid due date and time.'); return }
    if (form.kind && !chosen) { setError(`Choose which ${form.kind} to link, or choose No link.`); return }
    setBusy(true); setError('')
    const payload = {
      title: form.title.trim(),
      notes: form.notes.trim() || undefined,
      dueAt: due.toISOString(),
      priority: form.priority,
      linkTo: chosen?.value ?? null,
      linkLabel: chosen?.short ?? null,
    }
    try {
      if (item) { await api.patch(`/todos/${item.id}`, payload); try { await store.refresh({ keepCurrent: true }) } catch { store.notify('Saved. Refresh to see the latest list.', 'warn') } }
      else await store.addTodo(payload)
      onClose()
    } catch (e) { setError(e instanceof Error ? e.message : 'We could not save the to-do. Please try again.') }
    finally { setBusy(false) }
  }

  return (
    <Dialog
      open
      onClose={() => { if (!busy) onClose() }}
      title={item ? 'Edit to-do' : 'Add a to-do'}
      description="Only you see this item. You will get a reminder in the notification bell when it is due."
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={busy} onClick={() => void save()}>
            {busy ? 'Saving…' : item ? 'Save changes' : 'Add item'}
          </Button>
        </>
      }
    >
      {error && <p role="alert" className="td-form-error">{error}</p>}
      <fieldset disabled={busy} className="td-form">
        <Section icon={<ListChecks size={16} />} title="What needs doing" hint="A short title is all that is required.">
          <Field label="Title" required error={titleError} className="td-span-2">
            <Input
              value={form.title}
              maxLength={300}
              autoFocus
              aria-invalid={!!titleError}
              onChange={(e) => { set('title', e.target.value); if (titleError) setTitleError('') }}
              placeholder="Call the client about the interview slot"
            />
          </Field>
          <Field label="Notes" hint="Optional" className="td-span-2">
            <Textarea rows={2} value={form.notes} onChange={(e) => set('notes', e.target.value)} />
          </Field>
        </Section>

        <Section icon={<CalendarClock size={16} />} title="When" hint="You are reminded in the notification bell at this time.">
          <Field label="Due date and time" required>
            <DatePicker withTime required value={form.dueAt} onChange={(value) => set('dueAt', value)} />
          </Field>
          <div className="ui-field block">
            <span className="td-label" id="td-prio-label">Priority</span>
            <div className="td-seg" role="radiogroup" aria-labelledby="td-prio-label">
              {PRIORITIES.map((p) => (
                <button
                  key={p.value}
                  type="button"
                  role="radio"
                  aria-checked={form.priority === p.value}
                  className={cn(`is-${p.value}`, form.priority === p.value && 'is-on')}
                  onClick={() => set('priority', p.value)}
                >{p.label}</button>
              ))}
            </div>
          </div>
        </Section>

        <Section icon={<Link2 size={16} />} title="Link (optional)" hint="Pick a candidate, client or order so you can open it from the list.">
          <Field label="Link to">
            <Select
              value={form.kind}
              onChange={(e) => setForm((f) => ({ ...f, kind: e.target.value as LinkKind | '', link: '' }))}
            >
              <option value="">No link</option>
              <option value="candidate">Candidate</option>
              <option value="client">Client</option>
              <option value="order">Order</option>
            </Select>
          </Field>
          <Field label={form.kind ? LINK_KINDS[form.kind].label : 'Name'}>
            <Select value={form.link} disabled={!form.kind} onChange={(e) => set('link', e.target.value)} aria-label="Linked item">
              <option value="">{form.kind ? `Choose a ${form.kind}` : 'Choose what to link to first'}</option>
              {form.kind && recordOptions[form.kind].map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </Select>
          </Field>
        </Section>
      </fieldset>
    </Dialog>
  )
}
