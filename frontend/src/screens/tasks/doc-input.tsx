import { useEffect, useRef, useState, type ChangeEvent, type KeyboardEvent } from 'react'

/** "2026-11-19" -> "19/11/2026"; anything that is not a real date -> "". */
export function docToText(iso: string | null | undefined): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? '')
  return match ? `${match[3]}/${match[2]}/${match[1]}` : ''
}

/** "19/11/2026" (also 19-11-2026, 19.11.2026, 19112026) -> "2026-11-19" when it is a real day from 2000 to 2100, else null. */
export function docFromText(text: string): string | null {
  const digits = text.replace(/\D/g, '')
  if (digits.length !== 8) return null
  const [day, month, year] = [Number(digits.slice(0, 2)), Number(digits.slice(2, 4)), Number(digits.slice(4))]
  if (year < 2000 || year > 2100) return null
  const real = new Date(Date.UTC(year, month - 1, day))
  if (real.getUTCFullYear() !== year || real.getUTCMonth() !== month - 1 || real.getUTCDate() !== day) return null
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

/** What the person has typed so far, shaped as dd/mm/yyyy with the slashes added for them. */
export function shapeDocText(raw: string): string {
  const digits = raw.replace(/\D/g, '').slice(0, 8)
  if (digits.length <= 2) return digits
  if (digits.length <= 4) return `${digits.slice(0, 2)}/${digits.slice(2)}`
  return `${digits.slice(0, 2)}/${digits.slice(2, 4)}/${digits.slice(4)}`
}

/**
 * The DOC (date of commencement) typed right in the Tasks table: dd/mm/yyyy, slashes added as they type. Enter or leaving the box saves a
 * complete real date; clearing the box removes it; Escape puts the saved date back. A half-typed or impossible date is flagged and never sent.
 */
export function DocInput({ value, candidate, onSave }: { value: string | null; candidate: string; onSave: (iso: string | null) => Promise<void> }) {
  const saved = docToText(value)
  const [text, setText] = useState(saved)
  const [state, setState] = useState<'idle' | 'saving' | 'error'>('idle')
  const [note, setNote] = useState('')
  // Somebody else's change to this date (a live refresh) never throws away what is being typed, and a stale read landing during our own save
  // never flips the box back for a moment: the box follows the saved value only while it is neither focused nor being saved.
  const focused = useRef(false)
  const busy = useRef(false)
  useEffect(() => { busy.current = state === 'saving' }, [state])
  useEffect(() => { if (focused.current || busy.current) return; setText(saved); setState('idle'); setNote('') }, [saved])

  const commit = async () => {
    if (text === saved) return
    if (text === '') {
      setState('saving')
      try { await onSave(null); setState('idle') } catch (error) { setState('error'); setNote(error instanceof Error ? error.message : 'Could not save'); setText(saved) }
      return
    }
    const iso = docFromText(text)
    if (!iso) { setState('error'); setNote('Type the date as dd/mm/yyyy'); return }
    setState('saving')
    try { await onSave(iso); setState('idle'); setNote('') } catch (error) { setState('error'); setNote(error instanceof Error ? error.message : 'Could not save the date'); setText(saved) }
  }
  const onKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') { event.preventDefault(); void commit(); event.currentTarget.blur() }
    if (event.key === 'Escape') { setText(saved); setState('idle'); setNote(''); event.currentTarget.blur() }
  }
  return <input
    type="text" inputMode="numeric" autoComplete="off" maxLength={10} placeholder="dd/mm/yyyy"
    className={`dw-doc-input tnum${state === 'error' ? ' is-error' : ''}`} value={text}
    aria-label={`Date of commencement for ${candidate}, as dd/mm/yyyy`} aria-invalid={state === 'error' || undefined} aria-busy={state === 'saving' || undefined}
    disabled={state === 'saving'} title={note || 'Date of commencement: type dd/mm/yyyy and press Enter'}
    onChange={(event: ChangeEvent<HTMLInputElement>) => { setText(shapeDocText(event.target.value)); if (state === 'error') { setState('idle'); setNote('') } }}
    onFocus={() => { focused.current = true }} onBlur={() => { focused.current = false; void commit() }} onKeyDown={onKey} />
}
