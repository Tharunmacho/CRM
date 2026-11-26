import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown } from 'lucide-react'
import { initials } from '@/shared/lib/format'
import { cn } from '@/shared/lib/cn'
import { Button, CheckboxInput } from '@/shared/ui/primitives'
import { OWNERS_MAX, itemOwners, ownersFull, ownersLabel, type OwnerChoice, type RequirementItem } from '@/features/job-requirements/job-requirements-model'
import '@/styles/job-requirement-owners.css'

/** The same person always gets the same colour (the six avatar hues of the app). */
const hueOf = (id: string): number => [...id].reduce((sum, char) => sum + char.charCodeAt(0), 0) % 6
const sameSet = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((id) => b.includes(id))

/**
 * "Assigned to": who handles a requirement (one or several recruiters and the MD). The button shows initials and the names ("Asha, Ravi" or "Asha +2");
 * it opens a checklist popover (Select all, Clear). Nothing is sent while people are ticked: the choice is saved once, when the list is closed (Done, Escape,
 * a click outside), and only if it changed. Keyboard: Enter or Space opens it, Tab stays inside it, Escape closes it and returns to the button.
 */
export function OwnersCell({ item, choices, label, onSave }: { item: RequirementItem; choices: readonly OwnerChoice[]; /** For the screen reader: the row ("Asha Travels"). */ label: string; onSave: (ids: string[]) => void }) {
  const names = new Map(choices.map((choice) => [choice.id, choice.name]))
  const owners = itemOwners(item).map((owner) => ({ id: owner.id, name: owner.name || names.get(owner.id) || '' }))
  const ids = owners.map((owner) => owner.id)
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<string[]>([])
  const [at, setAt] = useState({ top: 0, left: 0, width: 260, maxHeight: 340 })
  const trigger = useRef<HTMLButtonElement>(null)
  const popup = useRef<HTMLDivElement>(null)
  const latest = useRef({ draft, ids, onSave })
  latest.current = { draft, ids, onSave }

  const place = useCallback(() => {
    const box = trigger.current?.getBoundingClientRect()
    if (!box) return
    const width = Math.min(Math.max(box.width, 260), window.innerWidth - 24)
    const wanted = Math.min(popup.current?.offsetHeight ?? 340, 340)
    const below = window.innerHeight - box.bottom - 12
    const above = box.top - 12
    const upward = below < Math.min(wanted, 240) && above > below
    const maxHeight = Math.max(160, Math.min(340, (upward ? above : below) - 4))
    setAt({ width, maxHeight, left: Math.max(12, Math.min(box.left, window.innerWidth - width - 12)), top: upward ? Math.max(12, box.top - Math.min(wanted, maxHeight) - 4) : box.bottom + 4 })
  }, [])

  /** Close the list; the choice is saved once, only when it differs from what the row had. */
  const finish = useCallback((focusButton: boolean) => {
    const { draft: picked, ids: before, onSave: save } = latest.current
    setOpen(false)
    if (!sameSet(picked, before)) save(picked)
    if (focusButton) trigger.current?.focus()
  }, [])

  useEffect(() => {
    if (!open) return undefined
    place()
    const frame = requestAnimationFrame(() => { place(); popup.current?.querySelector<HTMLElement>('input:not([disabled])')?.focus() })
    const onDown = (event: MouseEvent) => { const target = event.target as Node; if (!popup.current?.contains(target) && !trigger.current?.contains(target)) finish(false) }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); finish(true); return }
      if (event.key !== 'Tab' || !popup.current) return
      const items = Array.from(popup.current.querySelectorAll<HTMLElement>('input:not([disabled]), button:not([disabled])'))
      const first = items[0]
      const last = items[items.length - 1]
      if (!first) return
      if (event.shiftKey && (document.activeElement === first || !popup.current.contains(document.activeElement))) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && (document.activeElement === last || !popup.current.contains(document.activeElement))) { event.preventDefault(); first.focus() }
    }
    const onScroll = (event: Event) => { if (!popup.current?.contains(event.target as Node)) place() }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey, true)
    window.addEventListener('resize', place)
    window.addEventListener('scroll', onScroll, true)
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey, true)
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', onScroll, true)
    }
  }, [open, place, finish])

  const toggle = (id: string, on: boolean) => setDraft((current) => (on ? (current.includes(id) || current.length >= OWNERS_MAX ? current : [...current, id]) : current.filter((entry) => entry !== id)))
  const full = ownersFull(owners)
  const shown = owners.slice(0, 2)

  return (
    <div className="jro">
      <button ref={trigger} type="button" className={cn('jro-trigger', owners.length === 0 && 'is-empty', open && 'is-open')} aria-haspopup="dialog" aria-expanded={open}
        aria-label={`Assigned to, for ${label}: ${full}. Choose people`} title={owners.length > 1 ? full : undefined}
        onClick={() => { if (open) finish(false); else { setDraft(ids); setOpen(true) } }}>
        {shown.length > 0 && <span className="jro-avatars" aria-hidden="true">{shown.map((owner) => <span key={owner.id} className={cn('jro-avatar', `avatar-hue-${hueOf(owner.id)}`)}>{initials(owner.name || '?')}</span>)}</span>}
        <span className="jro-names">{ownersLabel(owners)}</span>
        <ChevronDown size={14} aria-hidden="true" className="jro-chev" />
      </button>
      {open && createPortal(
        <div ref={popup} className="jro-pop" role="dialog" aria-label={`Assigned to, for ${label}`} style={{ top: at.top, left: at.left, width: at.width, maxHeight: at.maxHeight }}>
          <div className="jro-head">
            <span>Assigned to</span>
            <span className="jro-acts">
              <button type="button" className="jro-link" disabled={choices.length === 0 || draft.length >= Math.min(choices.length, OWNERS_MAX)} onClick={() => setDraft((current) => [...current, ...choices.map((choice) => choice.id).filter((id) => !current.includes(id))].slice(0, OWNERS_MAX))}>Select all</button>
              <button type="button" className="jro-link" disabled={draft.length === 0} onClick={() => setDraft([])}>Clear</button>
            </span>
          </div>
          <div className="jro-list" role="group" aria-label="People">
            {choices.map((choice) => {
              const checked = draft.includes(choice.id)
              return (
                <label key={choice.id} className={cn('jro-row', checked && 'is-on')}>
                  <CheckboxInput checked={checked} disabled={!checked && draft.length >= OWNERS_MAX} aria-label={choice.name} onChange={(on) => toggle(choice.id, on)} />
                  <span className={cn('jro-avatar', `avatar-hue-${hueOf(choice.id)}`)} aria-hidden="true">{initials(choice.name)}</span>
                  <span className="jro-name">{choice.name}</span>
                </label>
              )
            })}
            {choices.length === 0 && <p className="jro-empty">There are no active recruiters yet.</p>}
          </div>
          <div className="jro-foot">
            <span role="status" aria-live="polite">{draft.length === 0 ? 'Nobody selected' : draft.length === 1 ? '1 person selected' : `${draft.length} people selected`}</span>
            <Button size="sm" variant="primary" onClick={() => finish(true)}>Done</Button>
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}
