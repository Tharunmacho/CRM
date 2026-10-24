import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { X, type LucideIcon } from 'lucide-react'

/** A flat section card (hairline border, neutral icon tile) used by every block of the Add Candidate form. */
export function EntrySection({ id, icon: Icon, title, meta, action, className = '', children }: {
  id?: string; icon: LucideIcon; title: string; meta?: ReactNode; action?: ReactNode; className?: string; children: ReactNode
}) {
  return (
    <section id={id} className={`ac-card ${className}`}>
      <header className="ac-card-head">
        <span className="ac-tile" aria-hidden><Icon size={17} /></span>
        <div className="ac-card-title">
          <h2>{title}</h2>
          {meta && <p>{meta}</p>}
        </div>
        {action && <div className="ac-card-action">{action}</div>}
      </header>
      {children}
    </section>
  )
}

const split = (value: string) => value.split(',').map((item) => item.trim()).filter(Boolean)

/**
 * Several values in one field, kept as the comma-separated text the form and the OCR merge already use.
 * Enter, comma or leaving the field turns what is typed into a chip; Backspace on an empty box removes the last one.
 */
export function TagField({ label, hint, required, value, onChange, suggestions = [], placeholder, className = '' }: {
  label: string; hint?: string; required?: boolean; value: string; onChange: (value: string) => void
  suggestions?: string[]; placeholder?: string; className?: string
}) {
  const id = useId()
  const list = `${id}-options`
  const input = useRef<HTMLInputElement>(null)
  const [draft, setDraft] = useState('')
  const tags = split(value)

  const commit = (raw: string) => {
    const incoming = split(raw).filter((item) => !tags.some((tag) => tag.toLowerCase() === item.toLowerCase()))
    setDraft('')
    if (incoming.length) onChange([...tags, ...incoming].join(', '))
  }
  const remove = (index: number) => onChange(tags.filter((_, position) => position !== index).join(', '))
  const onKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter' || event.key === ',') {
      if (event.key === 'Enter' && !draft.trim()) return // an empty Enter must not be swallowed
      event.preventDefault(); commit(draft)
    } else if (event.key === 'Backspace' && !draft && tags.length) {
      event.preventDefault(); remove(tags.length - 1)
    }
  }

  return (
    <div className={`ui-field block ac-tagfield ${className}`}>
      <label htmlFor={id} className="ac-tag-label">{label}{required && <span className="text-stamp-600"> *</span>}</label>
      <div className="ac-tags" onClick={() => input.current?.focus()}>
        {tags.map((tag, index) => (
          <span key={tag} className="ac-tag">
            {tag}
            <button type="button" aria-label={`Remove ${tag}`} onClick={(event) => { event.stopPropagation(); remove(index) }}><X size={12} aria-hidden /></button>
          </span>
        ))}
        <input
          ref={input} id={id} list={suggestions.length ? list : undefined} value={draft} autoComplete="off"
          placeholder={tags.length ? '' : placeholder} onKeyDown={onKey} onBlur={() => commit(draft)}
          onChange={(event) => {
            const text = event.target.value
            const picked = (event.nativeEvent as InputEvent).inputType === 'insertReplacementText' // chosen from the suggestion list
            if (text.includes(',') || picked) commit(text); else setDraft(text)
          }}
        />
        {suggestions.length > 0 && <datalist id={list}>{suggestions.map((item) => <option key={item} value={item} />)}</datalist>}
      </div>
      {hint && <span className="ac-hint">{hint}</span>}
    </div>
  )
}
