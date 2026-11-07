import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode, type Ref } from 'react'
import { Check, ChevronDown, Search, X } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { Avatar } from '@/features/clients/components/parts'
import { filterByText } from '@/features/jobs/job-search'
import '@/styles/jobs-simple.css'

/**
 * The dropdowns of the Jobs screens. Every one of them is searchable by typing (case and accent blind, every word), moves with the
 * arrow keys, chooses with Enter, closes with Escape (the list only, never the dialog around it) and says "No match" when nothing fits.
 */

/** A dropdown entry: a main line, an optional muted line, an optional small note on the right, and the texts a search looks in. */
export interface SelectItem {
  key: string
  label: string
  sub?: string
  note?: string
  search: string[]
  /** Show a soft avatar with the label's initials. */
  avatar?: boolean
}

/** Escape and the arrow keys for a list that opens under a field. */
function useListKeys(open: boolean, setOpen: (value: boolean) => void, count: number, cursor: number, setCursor: (fn: (n: number) => number) => void, choose: (index: number) => void) {
  return (event: KeyboardEvent) => {
    if (event.key === 'Escape' && open) {
      // Closes only the list; the dialog's own Escape handler listens on the document and must not also fire.
      event.preventDefault(); event.nativeEvent.stopPropagation(); setOpen(false); return
    }
    if (event.key === 'ArrowDown') { event.preventDefault(); if (!open) setOpen(true); else setCursor((n) => Math.min(count - 1, n + 1)) }
    else if (event.key === 'ArrowUp') { event.preventDefault(); setCursor((n) => Math.max(0, n - 1)) }
    else if (event.key === 'Enter' && open && count > 0 && cursor >= 0 && cursor < count) { event.preventDefault(); choose(cursor) }
  }
}

function ItemRow({ item, id, on, active, onPick, onHover }: { item: SelectItem; id: string; on: boolean; active: boolean; onPick: () => void; onHover: () => void }) {
  return <li id={id} role="option" aria-selected={on} aria-label={item.sub ? `${item.label} (${item.sub})` : item.label} className={cn(on && 'is-on', active && 'is-cursor')} onMouseEnter={onHover} onClick={onPick}>
    {item.avatar && <Avatar name={item.label} size="sm" />}
    <span className="js-opt-text"><b>{item.label}</b>{item.sub && <small>{item.sub}</small>}</span>
    {item.note && <span className="js-opt-jobs">{item.note}</span>}
    <Check size={15} className="js-opt-check" aria-hidden="true" />
  </li>
}

/**
 * A searchable single choice: a field-like button that opens a panel with a search box and the list. `popover` floats the panel under
 * the button (for toolbars); without it the panel opens in the page flow, so a dialog's own scrolling never clips it. `allItem` is an
 * entry such as "All clients" that is listed first while nothing is typed.
 */
export function SearchSelect({ items, value, onChange, placeholder, ariaLabel, searchPlaceholder = 'Type to search', noMatch = 'No match', emptyText, allItem, popover, compact, disabled, invalid }: {
  items: SelectItem[]
  /** The key of the chosen item, '' for the "all" entry. */
  value: string
  onChange: (item: SelectItem) => void
  placeholder: string
  ariaLabel: string
  searchPlaceholder?: string
  noMatch?: string
  /** Shown instead of "No match" when there is nothing at all to choose from. */
  emptyText?: string
  allItem?: SelectItem
  popover?: boolean
  compact?: boolean
  disabled?: boolean
  invalid?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [cursor, setCursor] = useState(0)
  const listId = useId()
  const box = useRef<HTMLDivElement>(null)
  const search = useRef<HTMLInputElement>(null)
  const selected = value ? items.find((item) => item.key === value) : allItem
  const shown = useMemo(() => {
    const found = filterByText(items, query, (item) => item.search)
    return allItem && !query.trim() ? [allItem, ...found] : found
  }, [items, query, allItem])

  useEffect(() => { setCursor(0) }, [query, open])
  useEffect(() => {
    if (!open) return undefined
    const away = (event: PointerEvent) => { if (box.current && !box.current.contains(event.target as Node)) setOpen(false) }
    document.addEventListener('pointerdown', away)
    return () => document.removeEventListener('pointerdown', away)
  }, [open])
  useEffect(() => {
    if (open) document.getElementById(`${listId}-${cursor}`)?.scrollIntoView({ block: 'nearest' })
  }, [cursor, open, listId])

  const choose = (index: number) => { const item = shown[index]; if (!item) return; onChange(item); setOpen(false); setQuery('') }
  const toggle = () => { setOpen((current) => !current); window.setTimeout(() => search.current?.focus(), 0) }
  const onKey = useListKeys(open, setOpen, shown.length, cursor, setCursor, choose)

  return <div className={cn('js-picker', popover && 'is-popover', compact && 'is-compact')} ref={box}>
    <button type="button" className="js-trigger" aria-haspopup="listbox" aria-expanded={open} aria-controls={listId} aria-label={ariaLabel} aria-invalid={invalid || undefined} disabled={disabled} onClick={toggle}
      onKeyDown={(event) => { if (!open && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) { event.preventDefault(); toggle() } }}>
      {selected
        ? <span className="js-trigger-value">{selected.avatar && <Avatar name={selected.label} size="sm" />}<span className="js-opt-text"><b>{selected.label}</b>{selected.sub && !compact && <small>{selected.sub}</small>}</span></span>
        : <span className="js-trigger-empty">{placeholder}</span>}
      <ChevronDown size={16} aria-hidden="true" />
    </button>
    {open && <div className="js-panel" onKeyDown={onKey}>
      <label className="js-search"><Search size={14} aria-hidden="true" />
        <input ref={search} type="text" role="combobox" aria-expanded="true" aria-controls={listId} aria-autocomplete="list" aria-activedescendant={shown[cursor] ? `${listId}-${cursor}` : undefined}
          value={query} onChange={(event) => setQuery(event.target.value)} placeholder={searchPlaceholder} aria-label={`Search: ${ariaLabel}`} autoComplete="off" spellCheck={false} />
      </label>
      <ul id={listId} role="listbox" aria-label={ariaLabel}>
        {shown.length === 0 && <li className="js-none" role="presentation">{items.length === 0 && emptyText ? emptyText : noMatch}</li>}
        {shown.map((item, index) => <ItemRow key={item.key || 'all'} item={item} id={`${listId}-${index}`} on={item.key === value} active={index === cursor} onPick={() => choose(index)} onHover={() => setCursor(index)} />)}
      </ul>
    </div>}
  </div>
}

/**
 * A text box that suggests saved names as you type but accepts any other text (a new job title, a country not used before). The list opens
 * in the page flow under the box. When nothing saved fits, a "No match" row says what will happen to the typed text.
 */
export function ComboInput({ value, onChange, options, ariaLabel, placeholder, noMatchNote, prefix, renderOption, normalize, invalid, disabled, maxLength, inputRef }: {
  value: string
  onChange: (value: string) => void
  options: readonly string[]
  ariaLabel: string
  placeholder?: string
  /** What the "No match" row adds after those words, from the typed text. */
  noMatchNote?: (typed: string) => string
  /** A small node drawn inside the box on the left (a flag). */
  prefix?: ReactNode
  /** Drawn before each suggestion (a flag). */
  renderOption?: (item: string) => ReactNode
  /** Applied to what was typed when the box is left ("KUWAIT" becomes "Kuwait"). */
  normalize?: (typed: string) => string
  invalid?: boolean
  disabled?: boolean
  maxLength?: number
  inputRef?: Ref<HTMLInputElement>
}) {
  const [open, setOpen] = useState(false)
  const [cursor, setCursor] = useState(-1)
  const listId = useId()
  const root = useRef<HTMLDivElement>(null)
  const typed = value.trim()
  const unique = useMemo(() => {
    const seen = new Set<string>()
    return options.map((option) => option.trim()).filter((option) => { const key = option.toLowerCase(); if (!option || seen.has(key)) return false; seen.add(key); return true })
  }, [options])
  const items = useMemo(() => filterByText(unique, value, (option) => [option]), [unique, value])
  const visible = open && !disabled && (items.length > 0 || typed.length > 0)

  useEffect(() => { setCursor(-1) }, [value, open])
  useEffect(() => {
    if (visible && cursor >= 0) document.getElementById(`${listId}-${cursor}`)?.scrollIntoView({ block: 'nearest' })
  }, [cursor, visible, listId])

  const pick = (index: number) => { const item = items[index]; if (item === undefined) return; onChange(item); setOpen(false) }
  const keys = useListKeys(visible, setOpen, items.length, cursor, setCursor, pick)
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown' && !visible) { event.preventDefault(); setOpen(true); setCursor(() => 0); return }
    keys(event)
  }

  // One control built as a row (flag, text, chevron are flex children of the same bordered box), so nothing can sit outside it.
  return <div className="js-combo" ref={root}>
    <div className={cn('js-control', invalid && 'is-invalid', disabled && 'is-disabled')}>
      {prefix && <span className="js-prefix">{prefix}</span>}
      <input ref={inputRef} className="js-control-input" type="text" role="combobox" aria-expanded={visible} aria-controls={visible ? listId : undefined} aria-autocomplete="list" aria-activedescendant={visible && cursor >= 0 ? `${listId}-${cursor}` : undefined}
        aria-label={ariaLabel} aria-invalid={invalid || undefined} placeholder={placeholder} maxLength={maxLength} disabled={disabled} autoComplete="off" spellCheck={false}
        value={value} onChange={(event) => { onChange(event.target.value); setOpen(true) }} onFocus={() => setOpen(true)} onClick={() => setOpen(true)}
        onBlur={(event) => {
          if (!root.current?.contains(event.relatedTarget as Node | null)) setOpen(false)
          if (normalize) { const next = normalize(value); if (next !== value) onChange(next) }
        }} onKeyDown={onKeyDown} />
      {unique.length > 0 && <ChevronDown size={16} aria-hidden="true" className="js-chevron" />}
    </div>
    {visible && <ul id={listId} role="listbox" aria-label={ariaLabel} className="js-combo-list" onMouseDown={(event) => event.preventDefault()} onClick={(event) => event.preventDefault()}>
      {items.length === 0 && <li className="js-none" role="presentation">No match{noMatchNote ? `. ${noMatchNote(typed)}` : ''}</li>}
      {items.map((item, index) => <li key={item} id={`${listId}-${index}`} role="option" aria-selected={item.toLowerCase() === typed.toLowerCase()} className={cn(index === cursor && 'is-cursor', item.toLowerCase() === typed.toLowerCase() && 'is-on')}
        onMouseEnter={() => setCursor(index)} onClick={() => pick(index)}>{renderOption?.(item)}<span className="js-opt-text"><b>{item}</b></span><Check size={15} className="js-opt-check" aria-hidden="true" /></li>)}
    </ul>}
  </div>
}

const splitSkill = (text: string) => text.split(',').map((item) => item.replace(/\s+/g, ' ').trim()).filter(Boolean)

/**
 * Several short values as chips in one bordered box (the required skills). Enter, a comma or leaving the box turns the typed text into a
 * chip; Backspace on an empty box removes the last one. Suggestions come from the values already in use; typing narrows them (case and
 * accent blind) and "No match" says that Enter adds the typed text.
 */
export function TagInput({ value, onChange, suggestions, ariaLabel, placeholder, invalid, disabled }: {
  value: string[]
  onChange: (value: string[]) => void
  suggestions: readonly string[]
  ariaLabel: string
  placeholder?: string
  invalid?: boolean
  disabled?: boolean
}) {
  const [draft, setDraft] = useState('')
  const [open, setOpen] = useState(false)
  const [cursor, setCursor] = useState(-1)
  const listId = useId()
  const root = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const has = (skill: string) => value.some((item) => item.toLowerCase() === skill.toLowerCase())
  const items = useMemo(() => filterByText(suggestions.filter((item) => !value.some((tag) => tag.toLowerCase() === item.toLowerCase())), draft, (item) => [item]).slice(0, 80), [suggestions, value, draft])
  const typed = draft.trim()
  const visible = open && !disabled && (items.length > 0 || typed.length > 0)

  useEffect(() => { setCursor(-1) }, [draft, open])
  useEffect(() => {
    if (visible && cursor >= 0) document.getElementById(`${listId}-${cursor}`)?.scrollIntoView({ block: 'nearest' })
  }, [cursor, visible, listId])

  const commit = (raw: string) => {
    const incoming = splitSkill(raw).filter((item) => !has(item))
    setDraft('')
    if (incoming.length) onChange([...value, ...incoming.filter((item, i) => incoming.findIndex((other) => other.toLowerCase() === item.toLowerCase()) === i)])
  }
  const remove = (index: number) => onChange(value.filter((_, position) => position !== index))
  const pick = (index: number) => { const item = items[index]; if (item !== undefined) { commit(item); setCursor(-1) } }
  const keys = useListKeys(visible, setOpen, items.length, cursor, setCursor, pick)
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown' && !visible) { event.preventDefault(); setOpen(true); setCursor(() => 0); return }
    if (event.key === 'Enter' && visible && cursor >= 0 && items[cursor] !== undefined) { keys(event); return }
    if (event.key === 'Enter' || event.key === ',') {
      if (event.key === 'Enter' && !typed) return // an empty Enter must not be swallowed
      event.preventDefault(); commit(draft)
    } else if (event.key === 'Backspace' && !draft && value.length) {
      event.preventDefault(); remove(value.length - 1)
    } else keys(event)
  }

  return <div className="js-combo" ref={root}>
    <div className={cn('js-control is-tags', invalid && 'is-invalid', disabled && 'is-disabled')} onClick={() => input.current?.focus()}>
      {value.map((skill, index) => <span key={skill} className="js-tag">{skill}
        <button type="button" aria-label={`Remove ${skill}`} disabled={disabled} onClick={(event) => { event.stopPropagation(); remove(index) }}><X size={12} aria-hidden="true" /></button></span>)}
      <input ref={input} className="js-control-input" type="text" role="combobox" aria-expanded={visible} aria-controls={visible ? listId : undefined} aria-autocomplete="list"
        aria-activedescendant={visible && cursor >= 0 ? `${listId}-${cursor}` : undefined} aria-label={ariaLabel} placeholder={value.length ? '' : placeholder} disabled={disabled} autoComplete="off" spellCheck={false}
        value={draft} maxLength={100} onFocus={() => setOpen(true)}
        onBlur={(event) => { if (!root.current?.contains(event.relatedTarget as Node | null)) { setOpen(false); commit(draft) } }}
        onChange={(event) => { const text = event.target.value; if (text.includes(',')) commit(text); else { setDraft(text); setOpen(true) } }} onKeyDown={onKeyDown} />
    </div>
    {visible && <ul id={listId} role="listbox" aria-label={ariaLabel} className="js-combo-list" onMouseDown={(event) => event.preventDefault()} onClick={(event) => event.preventDefault()}>
      {items.length === 0 && <li className="js-none" role="presentation">No match. Press Enter to add “{typed}”.</li>}
      {items.map((item, index) => <li key={item} id={`${listId}-${index}`} role="option" aria-selected={false} className={cn(index === cursor && 'is-cursor')}
        onMouseEnter={() => setCursor(index)} onClick={() => pick(index)}><span className="js-opt-text"><b>{item}</b></span></li>)}
    </ul>}
  </div>
}
