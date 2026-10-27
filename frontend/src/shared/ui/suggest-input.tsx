import {
  forwardRef,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type InputHTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { Input } from './primitives'

const MAX_SHOWN = 60

/** Unique, trimmed suggestions that match what has been typed; names that start with it come first. */
export function matchSuggestions(options: readonly string[], query: string): string[] {
  const needle = query.trim().toLowerCase()
  const seen = new Set<string>()
  const unique: string[] = []
  for (const option of options) {
    const text = option.trim()
    const key = text.toLowerCase()
    if (!text || seen.has(key)) continue
    seen.add(key)
    unique.push(text)
  }
  if (!needle) return unique.slice(0, MAX_SHOWN)
  const found = unique.filter((text) => text.toLowerCase().includes(needle) && text.toLowerCase() !== needle)
  found.sort((a, b) => Number(b.toLowerCase().startsWith(needle)) - Number(a.toLowerCase().startsWith(needle)))
  return found.slice(0, MAX_SHOWN)
}

interface SuggestOptions {
  /** The element the list opens under (the input, or the box around it). */
  anchor: RefObject<HTMLElement | null>
  options: readonly string[]
  query: string
  onPick: (value: string) => void
}

/**
 * Our own suggestion list for a text box. The browser's built-in list is not used: it is controlled
 * by the browser, looks different everywhere, and for address-like fields (Country, City) Chrome
 * shows the person's saved addresses instead, which hides the list.
 */
export function useSuggestions({ anchor, options, query, onPick }: SuggestOptions) {
  const listId = useId()
  const popupRef = useRef<HTMLDivElement>(null)
  const optionRefs = useRef<Record<number, HTMLButtonElement | null>>({})
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const [position, setPosition] = useState({ top: 0, left: 0, width: 220, maxHeight: 260 })
  const items = useMemo(() => matchSuggestions(options, query), [options, query])
  const visible = open && items.length > 0

  useEffect(() => { setActive(-1) }, [query, open])
  useEffect(() => {
    if (!visible) return
    const place = () => {
      const box = anchor.current?.getBoundingClientRect()
      if (!box) return
      const width = Math.min(Math.max(box.width, 200), window.innerWidth - 24)
      const wanted = Math.min(popupRef.current?.scrollHeight ?? 260, 270)
      const below = window.innerHeight - box.bottom - 12
      const above = box.top - 12
      const placeAbove = below < Math.min(wanted, 180) && above > below
      const maxHeight = Math.max(96, Math.min(270, (placeAbove ? above : below) - 6))
      setPosition({
        width,
        maxHeight,
        left: Math.max(12, Math.min(box.left, window.innerWidth - width - 12)),
        top: placeAbove ? Math.max(12, box.top - Math.min(wanted, maxHeight) - 6) : box.bottom + 6,
      })
    }
    place()
    const observer = new ResizeObserver(place)
    if (popupRef.current) observer.observe(popupRef.current)
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
    }
  }, [visible, anchor, items.length])
  useEffect(() => {
    if (visible && active >= 0) optionRefs.current[active]?.scrollIntoView({ block: 'nearest' })
  }, [active, visible])

  const pick = (value: string) => { setOpen(false); onPick(value) }
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (!items.length) return
      event.preventDefault()
      if (!open) { setOpen(true); return }
      const step = event.key === 'ArrowDown' ? 1 : -1
      setActive((current) => (current < 0 ? (step > 0 ? 0 : items.length - 1) : (current + step + items.length) % items.length))
    } else if (event.key === 'Enter' && visible && active >= 0) {
      event.preventDefault()
      event.stopPropagation()
      pick(items[active])
    } else if (event.key === 'Escape' && visible) {
      event.preventDefault()
      event.stopPropagation()
      setOpen(false)
    } else if (event.key === 'Tab') setOpen(false)
  }

  const popup: ReactNode = visible
    ? createPortal(
        <div
          ref={popupRef}
          className="custom-select-popup suggest-popup"
          style={position}
          // Keep the cursor in the text box while the list is used.
          onMouseDown={(event) => event.preventDefault()}
        >
          <div id={listId} role="listbox" className="custom-select-options" style={{ maxHeight: position.maxHeight - 12 }}>
            {items.map((item, index) => (
              <button
                key={item}
                ref={(element) => { optionRefs.current[index] = element }}
                id={`${listId}-${index}`}
                type="button"
                role="option"
                aria-selected={index === active}
                tabIndex={-1}
                className={cn('custom-select-option', index === active && 'is-active')}
                onMouseMove={() => setActive(index)}
                onClick={() => pick(item)}
              >
                <span>{item}</span>
              </button>
            ))}
          </div>
        </div>,
        document.body,
      )
    : null

  return {
    popup,
    open,
    setOpen,
    inputProps: {
      role: 'combobox' as const,
      'aria-autocomplete': 'list' as const,
      'aria-expanded': visible,
      'aria-controls': visible ? listId : undefined,
      'aria-activedescendant': visible && active >= 0 ? `${listId}-${active}` : undefined,
      // "off" is ignored by Chrome for address-like fields, so an unknown word is used to stop its saved-address list.
      autoComplete: 'nope',
      autoCorrect: 'off',
      spellCheck: false,
      'data-lpignore': 'true',
      'data-form-type': 'other',
      onKeyDown,
    },
  }
}

type SuggestInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'list'> & {
  value: string
  onChange: (value: string) => void
  /** Names to suggest. People can still type anything else. */
  options: readonly string[]
  wrapperClassName?: string
}

/** A text box with our own suggestion list: click or focus it to see every suggestion, type to narrow them. */
export const SuggestInput = forwardRef<HTMLInputElement, SuggestInputProps>(function SuggestInput(
  { value, onChange, options, className, wrapperClassName, onFocus, onBlur, onClick, onKeyDown, name, ...rest },
  forwardedRef,
) {
  const wrapper = useRef<HTMLSpanElement>(null)
  const fallbackName = useId()
  const { popup, setOpen, inputProps } = useSuggestions({ anchor: wrapper, options, query: value, onPick: onChange })
  const hasOptions = options.length > 0
  return (
    <span ref={wrapper} className={cn('relative block w-full', wrapperClassName)}>
      <Input
        {...rest}
        {...inputProps}
        ref={forwardedRef}
        name={name ?? `crm-${fallbackName}`}
        className={cn(hasOptions && 'pr-9', className)}
        value={value}
        onChange={(event) => { onChange(event.target.value); setOpen(true) }}
        onFocus={(event) => { setOpen(true); onFocus?.(event) }}
        onClick={(event) => { setOpen(true); onClick?.(event) }}
        onBlur={(event) => { setOpen(false); onBlur?.(event) }}
        onKeyDown={(event) => { inputProps.onKeyDown(event); onKeyDown?.(event) }}
      />
      {hasOptions && <ChevronDown size={15} aria-hidden="true" className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[var(--text-light)]" />}
      {popup}
    </span>
  )
})
