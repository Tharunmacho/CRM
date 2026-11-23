import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { BellOff, Trash2 } from 'lucide-react'
import { api } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { formatPhone } from '@/shared/lib/phone'
import { homeDigits } from '@/shared/lib/agency'
import { smartTime } from '@/shared/lib/format'
import { Button, Field, Input } from '@/shared/ui/primitives'
import { Panel, PanelHeader } from '@/shared/ui/display'
import { Dialog } from '@/shared/ui/overlay'
import './ignored-numbers.css'

export interface IgnoredNumber { id: string; number: string; label: string; createdAt: string }

/**
 * "Numbers to ignore" (admin only, in the WhatsApp Number settings tab). Staff, agents, clients and spammers share the business number with
 * candidates; a file from a number on this list is kept as "Skipped" (never read, never a candidate) and the number gets no automatic reply.
 * Staff can still write to such a number by hand. The server compares numbers the CRM's way ("+91 98765 43210", "9876543210" and
 * "09876543210" are one number), so the list shows each number once, formatted with the agency's calling code.
 */
export function IgnoredNumbers() {
  const store = useStore()
  const home = homeDigits(store.settings?.homeCallingCode)
  const [items, setItems] = useState<IgnoredNumber[] | null>(null)
  const [loadError, setLoadError] = useState('')
  const [number, setNumber] = useState('')
  const [label, setLabel] = useState('')
  const [formError, setFormError] = useState('')
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<IgnoredNumber | null>(null)
  const [removeBusy, setRemoveBusy] = useState(false)
  const [removeError, setRemoveError] = useState('')
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const result = await api.get<{ items: IgnoredNumber[] }>('/whatsapp/ignored-numbers', { signal })
      if (!signal?.aborted && alive.current) { setItems(result.items); setLoadError('') }
    } catch (error) {
      if (!signal?.aborted && alive.current) setLoadError(error instanceof Error ? error.message : 'We could not load the list. Try again.')
    }
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal)
    return () => controller.abort()
  }, [load, store.me.id])

  async function add(event: FormEvent) {
    event.preventDefault()
    if (adding) return
    if (!number.trim()) { setFormError('Type the phone number to ignore.'); return }
    setAdding(true); setFormError('')
    try {
      await api.post('/whatsapp/ignored-numbers', { number: number.trim(), label: label.trim() })
      if (!alive.current) return
      setNumber(''); setLabel('')
      await load()
    } catch (error) {
      if (alive.current) setFormError(error instanceof Error ? error.message : 'We could not add this number. Try again.')
    } finally { if (alive.current) setAdding(false) }
  }

  async function remove() {
    if (!removing || removeBusy) return
    setRemoveBusy(true); setRemoveError('')
    try {
      await api.del(`/whatsapp/ignored-numbers/${encodeURIComponent(removing.id)}`)
      if (!alive.current) return
      setRemoving(null)
      await load()
    } catch (error) {
      if (alive.current) setRemoveError(error instanceof Error ? error.message : 'We could not remove this number. Try again.')
    } finally { if (alive.current) setRemoveBusy(false) }
  }

  return <Panel className="wa-ignore">
    <PanelHeader title="Numbers to ignore" meta={items ? `${items.length} ${items.length === 1 ? 'number' : 'numbers'}` : undefined} />
    <p className="wa-ignore-intro">Add the numbers of staff, agents, clients or anyone who should never become a candidate. Their files are kept as Skipped (not read) and they get no automatic reply. You can still write to them yourself.</p>
    <form className="wa-ignore-form" onSubmit={(event) => void add(event)} noValidate>
      <Field label="Phone number" hint="Add the country code for a number outside your own country, for example +44 7700 900123." error={formError}>
        <Input type="tel" inputMode="tel" autoComplete="off" maxLength={40} value={number} placeholder="+91 98765 43210"
          onChange={(event) => { setNumber(event.target.value); if (formError) setFormError('') }} />
      </Field>
      <Field label="Note" hint="Who is this, or why ignore it? (optional)">
        <Input type="text" autoComplete="off" maxLength={200} value={label} placeholder="Recruiter, agent, client ..."
          onChange={(event) => setLabel(event.target.value)} />
      </Field>
      <Button type="submit" variant="primary" disabled={adding}>{adding ? 'Adding…' : 'Add number'}</Button>
    </form>
    {loadError && <div role="alert" className="wa-ignore-error"><span>{loadError}</span><Button size="sm" onClick={() => void load()}>Try again</Button></div>}
    {!items && !loadError && <p className="wa-ignore-state" role="status">Loading the list…</p>}
    {items && items.length === 0 && <p className="wa-ignore-state"><BellOff size={16} aria-hidden="true" /> No numbers are ignored. Every sender is treated as a possible candidate.</p>}
    {items && items.length > 0 && <ul className="wa-ignore-list" aria-label="Ignored numbers">
      {items.map((item) => {
        const shown = formatPhone(item.number, home) || item.number
        return <li key={item.id}>
          <div className="wa-ignore-who"><strong className="tnum">{shown}</strong><span>{item.label || 'No note'}</span></div>
          <time className="wa-ignore-when" dateTime={item.createdAt}>Added {smartTime(item.createdAt)}</time>
          <Button size="sm" variant="dangerGhost" icon={<Trash2 size={14} aria-hidden="true" />} aria-label={`Stop ignoring ${shown}`}
            onClick={() => { setRemoving(item); setRemoveError('') }}>Remove</Button>
        </li>
      })}
    </ul>}
    <Dialog open={!!removing} onClose={() => { if (!removeBusy) setRemoving(null) }} title="Stop ignoring this number?"
      description={removing ? `${formatPhone(removing.number, home) || removing.number}${removing.label ? ` · ${removing.label}` : ''}` : undefined}
      footer={<><Button variant="ghost" disabled={removeBusy} onClick={() => setRemoving(null)}>Cancel</Button><Button variant="danger" disabled={removeBusy} onClick={() => void remove()}>{removeBusy ? 'Removing…' : 'Remove from the list'}</Button></>}>
      {removeError && <p role="alert" className="wa-ignore-error">{removeError}</p>}
      <p className="wa-ignore-confirm">From now on their documents are read again and may become candidates. Files already skipped stay skipped.</p>
    </Dialog>
  </Panel>
}
