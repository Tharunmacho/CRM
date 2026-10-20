import {
  Children,
  Fragment,
  forwardRef,
  isValidElement,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
  type SelectHTMLAttributes,
} from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown, Search } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { CalendarContext, useFieldLabel } from './control-context'

interface Option {
  value: string
  label: string
  disabled: boolean
  group?: string
}
interface OptionProps {
  value?: string | number
  children?: ReactNode
  disabled?: boolean
  label?: string
}

function optionText(children: ReactNode): string {
  return Children.toArray(children)
    .map((child) =>
      isValidElement<OptionProps>(child) ? optionText(child.props.children) : String(child),
    )
    .join('')
}

function collectOptions(children: ReactNode, group?: string, disabled = false): Option[] {
  return Children.toArray(children).flatMap((child) => {
    if (!isValidElement<OptionProps>(child)) return []
    if (child.type === 'option') {
      const label = child.props.label ?? optionText(child.props.children)
      return [
        {
          value: String(child.props.value ?? label),
          label,
          disabled: disabled || Boolean(child.props.disabled),
          group,
        },
      ]
    }
    if (child.type === 'optgroup' || child.type === Fragment)
      return collectOptions(
        child.props.children,
        child.props.label ?? group,
        disabled || Boolean(child.props.disabled),
      )
    return []
  })
}

export const Select = forwardRef<
  HTMLSelectElement,
  SelectHTMLAttributes<HTMLSelectElement> & { compact?: boolean }
>(function Select(
  {
    children,
    className,
    value,
    defaultValue,
    onChange,
    disabled,
    multiple,
    compact,
    id,
    style,
    'aria-label': ariaLabel,
    'aria-labelledby': labelledBy,
    ...rest
  },
  forwardedRef,
) {
  const fieldLabel = useFieldLabel()
  const calendarOwner = useContext(CalendarContext)
  const label = ariaLabel ?? fieldLabel ?? 'Select an option'
  const listId = useId()
  const nativeRef = useRef<HTMLSelectElement | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popupRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const optionRefs = useRef<Record<number, HTMLButtonElement | null>>({})
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const options = collectOptions(children)
  const [internalValue, setInternalValue] = useState<string | string[]>(() =>
    Array.isArray(defaultValue)
      ? [...defaultValue]
      : String(defaultValue ?? options[0]?.value ?? ''),
  )
  const currentValue = value ?? internalValue
  const selectedValues = Array.isArray(currentValue)
    ? currentValue.map(String)
    : [String(currentValue)]
  const selectedOptions = options.filter((option) => selectedValues.includes(option.value))
  const display =
    selectedOptions.map((option) => option.label).join(', ') ||
    options[0]?.label ||
    'Select an option'
  const filtered = options.filter((option) =>
    option.label.toLowerCase().includes(query.trim().toLowerCase()),
  )
  const searchable = options.length > 8
  const [position, setPosition] = useState({ top: 0, left: 0, width: 220, maxHeight: 304 })

  const close = (restoreFocus = true) => {
    setOpen(false)
    if (restoreFocus) triggerRef.current?.focus()
  }
  const show = () => {
    if (disabled) return
    setQuery('')
    const selectedIndex = options.findIndex(
      (option) => selectedValues.includes(option.value) && !option.disabled,
    )
    setActiveIndex(
      selectedIndex >= 0
        ? selectedIndex
        : Math.max(
            0,
            options.findIndex((option) => !option.disabled),
          ),
    )
    setOpen(true)
  }
  const choose = (option: Option) => {
    const native = nativeRef.current
    if (!native || option.disabled) return
    if (multiple) {
      const next = selectedValues.includes(option.value)
        ? selectedValues.filter((item) => item !== option.value)
        : [...selectedValues, option.value]
      for (const nativeOption of native.options)
        nativeOption.selected = next.includes(nativeOption.value)
    } else native.value = option.value
    native.dispatchEvent(new Event('change', { bubbles: true }))
    if (!multiple) close()
  }
  const handleKeys = (event: KeyboardEvent<HTMLElement>) => {
    if (!open) {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
        event.preventDefault()
        show()
      }
      return
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      close()
      return
    }
    if (event.key === 'Tab') {
      event.stopPropagation()
      close()
      return
    }
    if (event.key === 'Enter' || (event.key === ' ' && event.target !== searchRef.current)) {
      event.preventDefault()
      event.stopPropagation()
      if (filtered[activeIndex]) choose(filtered[activeIndex])
      return
    }
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      if (['Home', 'End'].includes(event.key) && event.target === searchRef.current && query) return
      event.preventDefault()
      const enabled = filtered
        .map((option, index) => (option.disabled ? -1 : index))
        .filter((index) => index >= 0)
      const current = enabled.indexOf(activeIndex)
      const next =
        event.key === 'Home'
          ? enabled[0]
          : event.key === 'End'
            ? enabled[enabled.length - 1]
            : enabled[
                (current + (event.key === 'ArrowDown' ? 1 : -1) + enabled.length) % enabled.length
              ]
      if (next !== undefined) setActiveIndex(next)
    } else if (!searchable && event.key.length === 1) {
      const next = filtered.findIndex(
        (option) =>
          !option.disabled && option.label.toLowerCase().startsWith(event.key.toLowerCase()),
      )
      if (next >= 0) setActiveIndex(next)
    }
  }

  useEffect(() => {
    if (!open) return
    const reposition = () => {
      const trigger = triggerRef.current?.getBoundingClientRect()
      if (!trigger) return
      const width = Math.min(Math.max(trigger.width, compact ? 170 : 220), window.innerWidth - 24)
      const height = Math.min(popupRef.current?.scrollHeight ?? 304, 304)
      const below = window.innerHeight - trigger.bottom - 12
      const above = trigger.top - 12
      const placeAbove = below < height && above > below
      const maxHeight = Math.max(100, Math.min(304, (placeAbove ? above : below) - 8))
      setPosition({
        width,
        left: Math.max(12, Math.min(trigger.left, window.innerWidth - width - 12)),
        top: placeAbove
          ? Math.max(12, trigger.top - Math.min(height, maxHeight) - 6)
          : trigger.bottom + 6,
        maxHeight,
      })
    }
    const outside = (event: PointerEvent) => {
      if (
        !popupRef.current?.contains(event.target as Node) &&
        !triggerRef.current?.contains(event.target as Node)
      )
        close(false)
    }
    reposition()
    const resizeObserver = new ResizeObserver(reposition)
    if (popupRef.current) resizeObserver.observe(popupRef.current)
    if (searchable) searchRef.current?.focus({ preventScroll: true })
    window.addEventListener('resize', reposition)
    window.addEventListener('scroll', reposition, true)
    document.addEventListener('pointerdown', outside)
    return () => {
      resizeObserver.disconnect()
      window.removeEventListener('resize', reposition)
      window.removeEventListener('scroll', reposition, true)
      document.removeEventListener('pointerdown', outside)
    }
  }, [open, compact, searchable])

  useEffect(() => {
    if (open) optionRefs.current[activeIndex]?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex, open])
  useEffect(() => {
    if (disabled) setOpen(false)
  }, [disabled])

  return (
    <span className={cn('custom-select', compact && 'custom-select-compact')}>
      <select
        {...rest}
        ref={(element) => {
          nativeRef.current = element
          if (typeof forwardedRef === 'function') forwardedRef(element)
          else if (forwardedRef) forwardedRef.current = element
        }}
        hidden
        tabIndex={-1}
        aria-hidden="true"
        value={value}
        defaultValue={defaultValue}
        multiple={multiple}
        disabled={disabled}
        onChange={(event) => {
          if (value === undefined)
            setInternalValue(
              multiple
                ? [...event.target.selectedOptions].map((option) => option.value)
                : event.target.value,
            )
          onChange?.(event)
        }}
      >
        {children}
      </select>
      <button
        ref={triggerRef}
        id={id}
        type="button"
        role="combobox"
        aria-label={labelledBy ? undefined : label}
        aria-labelledby={labelledBy}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-activedescendant={
          open && filtered[activeIndex] ? `${listId}-${activeIndex}` : undefined
        }
        aria-required={rest.required}
        aria-invalid={rest['aria-invalid']}
        aria-describedby={rest['aria-describedby']}
        disabled={disabled}
        style={style}
        className={cn('custom-select-trigger', compact ? 'h-8' : 'h-10', className)}
        onClick={() => (open ? close() : show())}
        onKeyDown={handleKeys}
      >
        <span>{display}</span>
        <ChevronDown size={15} aria-hidden="true" />
      </button>
      {open &&
        createPortal(
          <div
            ref={popupRef}
            className="custom-select-popup"
            data-calendar-owner={calendarOwner}
            style={position}
            onKeyDown={handleKeys}
          >
            {searchable && (
              <div className="custom-select-search">
                <Search size={14} />
                <input
                  ref={searchRef}
                  aria-label={`Search ${label}`}
                  autoComplete="off"
                  placeholder="Search options..."
                  value={query}
                  onChange={(event) => {
                    setQuery(event.target.value)
                    setActiveIndex(0)
                  }}
                />
              </div>
            )}
            <div
              id={listId}
              role="listbox"
              aria-label={label}
              aria-multiselectable={multiple || undefined}
              className="custom-select-options"
            >
              {filtered.length ? (
                filtered.map((option, index) => (
                  <div key={`${option.value}-${index}`}>
                    {option.group &&
                      (index === 0 || filtered[index - 1].group !== option.group) && (
                        <div className="custom-select-group">{option.group}</div>
                      )}
                    <button
                      ref={(element) => {
                        optionRefs.current[index] = element
                      }}
                      id={`${listId}-${index}`}
                      type="button"
                      role="option"
                      aria-selected={selectedValues.includes(option.value)}
                      aria-disabled={option.disabled || undefined}
                      disabled={option.disabled}
                      tabIndex={-1}
                      className={cn(
                        'custom-select-option',
                        index === activeIndex && 'is-active',
                        selectedValues.includes(option.value) && 'is-selected',
                      )}
                      onMouseMove={() => {
                        if (!option.disabled) setActiveIndex(index)
                      }}
                      onClick={() => choose(option)}
                    >
                      <span>{option.label}</span>
                      {selectedValues.includes(option.value) && <Check size={14} />}
                    </button>
                  </div>
                ))
              ) : (
                <p className="custom-select-empty">No matching options</p>
              )}
            </div>
          </div>,
          document.body,
        )}
    </span>
  )
})
