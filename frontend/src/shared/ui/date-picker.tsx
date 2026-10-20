import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { CalendarDays, ChevronLeft, ChevronRight, Clock3, X } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { CalendarContext, useFieldLabel } from './control-context'
import { Select } from './select'

interface DatePickerProps {
  value: string
  onChange: (value: string) => void
  withTime?: boolean
  disabled?: boolean
  readOnly?: boolean
  required?: boolean
  min?: string
  max?: string
  name?: string
  id?: string
  placeholder?: string
  className?: string
  compact?: boolean
  'aria-label'?: string
}

const MONTHS = Array.from({ length: 12 }, (_, index) =>
  new Date(2000, index, 1).toLocaleDateString('en-GB', { month: 'long' }),
)
const WEEKDAYS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']

function dateKey(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function parseDate(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}/.test(value)) return null
  const [year, month, day] = value.slice(0, 10).split('-').map(Number)
  const date = new Date(year, month - 1, day)
  return dateKey(date) === value.slice(0, 10) ? date : null
}

function shiftMonth(date: Date, amount: number) {
  const lastDay = new Date(date.getFullYear(), date.getMonth() + amount + 1, 0).getDate()
  return new Date(date.getFullYear(), date.getMonth() + amount, Math.min(date.getDate(), lastDay))
}

export function DatePicker({
  value,
  onChange,
  withTime,
  disabled,
  readOnly,
  required,
  min,
  max,
  name,
  id,
  placeholder,
  className,
  compact,
  'aria-label': ariaLabel,
}: DatePickerProps) {
  const fieldLabel = useFieldLabel()
  const label = ariaLabel ?? fieldLabel ?? (withTime ? 'Choose date and time' : 'Choose date')
  const popupId = useId()
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popupRef = useRef<HTMLDivElement>(null)
  const dayRefs = useRef<Record<string, HTMLButtonElement | null>>({})
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState(value)
  const [visibleMonth, setVisibleMonth] = useState(() => parseDate(value) ?? new Date())
  const [focusedDay, setFocusedDay] = useState(() => dateKey(parseDate(value) ?? new Date()))
  const [position, setPosition] = useState({ top: 0, left: 0, maxHeight: 480 })
  const selectedDate = parseDate(draft)
  const today = dateKey(new Date())
  const time = draft.split('T')[1]?.slice(0, 5) ?? '09:00'
  const minDay = min?.slice(0, 10)
  const maxDay = max?.slice(0, 10)
  const allowed = (key: string) => (!minDay || key >= minDay) && (!maxDay || key <= maxDay)
  const firstDay = new Date(visibleMonth.getFullYear(), visibleMonth.getMonth(), 1)
  const gridStart = new Date(firstDay)
  gridStart.setDate(1 - ((firstDay.getDay() + 6) % 7))
  const days = Array.from(
    { length: 42 },
    (_, index) =>
      new Date(gridStart.getFullYear(), gridStart.getMonth(), gridStart.getDate() + index),
  )
  const earliestYear = Math.min(
    1900,
    visibleMonth.getFullYear(),
    parseDate(min ?? '')?.getFullYear() ?? 1900,
  )
  const latestYear = Math.max(
    new Date().getFullYear() + 20,
    visibleMonth.getFullYear(),
    parseDate(max ?? '')?.getFullYear() ?? 0,
  )
  const years = Array.from(
    { length: latestYear - earliestYear + 1 },
    (_, index) => latestYear - index,
  )

  const close = (restoreFocus = true) => {
    setOpen(false)
    if (restoreFocus) triggerRef.current?.focus()
  }
  const focusDate = (date: Date) => {
    let key = dateKey(date)
    if (minDay && key < minDay) key = minDay
    if (maxDay && key > maxDay) key = maxDay
    const next = parseDate(key) ?? date
    setVisibleMonth(next)
    setFocusedDay(dateKey(next))
  }
  const commit = (next: string) => {
    onChange(next)
    close()
  }
  const chooseDay = (date: Date) => {
    const key = dateKey(date)
    if (!allowed(key)) return
    if (withTime) {
      setDraft(`${key}T${time}`)
      setVisibleMonth(date)
      setFocusedDay(key)
    } else commit(key)
  }
  const handleKeys = (event: KeyboardEvent<HTMLButtonElement>, date: Date) => {
    const next = new Date(date)
    const offsets: Record<string, number> = {
      ArrowLeft: -1,
      ArrowRight: 1,
      ArrowUp: -7,
      ArrowDown: 7,
    }
    if (event.key in offsets) next.setDate(next.getDate() + offsets[event.key])
    else if (event.key === 'Home') next.setDate(next.getDate() - ((next.getDay() + 6) % 7))
    else if (event.key === 'End') next.setDate(next.getDate() + 6 - ((next.getDay() + 6) % 7))
    else if (event.key === 'PageUp' || event.key === 'PageDown') {
      event.preventDefault()
      focusDate(shiftMonth(date, (event.key === 'PageUp' ? -1 : 1) * (event.shiftKey ? 12 : 1)))
      return
    } else return
    event.preventDefault()
    focusDate(next)
  }

  useEffect(() => {
    if (!open) return
    const reposition = () => {
      const trigger = triggerRef.current?.getBoundingClientRect()
      if (!trigger) return
      const width = Math.min(320, window.innerWidth - 24)
      const height = popupRef.current?.offsetHeight ?? (withTime ? 440 : 365)
      const below = window.innerHeight - trigger.bottom - 12
      const above = trigger.top - 12
      const placeAbove = below < height && above > below
      const maxHeight = Math.max(180, (placeAbove ? above : below) - 8)
      setPosition({
        left: Math.max(12, Math.min(trigger.left, window.innerWidth - width - 12)),
        top: placeAbove
          ? Math.max(12, trigger.top - Math.min(height, maxHeight) - 8)
          : trigger.bottom + 8,
        maxHeight,
      })
    }
    const outside = (event: PointerEvent) => {
      if (
        event.target instanceof Element &&
        event.target.closest(`[data-calendar-owner="${popupId}"]`)
      )
        return
      if (
        popupRef.current?.contains(event.target as Node) ||
        triggerRef.current?.contains(event.target as Node)
      )
        return
      close(false)
    }
    reposition()
    window.addEventListener('resize', reposition)
    window.addEventListener('scroll', reposition, true)
    document.addEventListener('pointerdown', outside)
    return () => {
      window.removeEventListener('resize', reposition)
      window.removeEventListener('scroll', reposition, true)
      document.removeEventListener('pointerdown', outside)
    }
  }, [open, withTime])

  useEffect(() => {
    if (open) dayRefs.current[focusedDay]?.focus({ preventScroll: true })
  }, [open, focusedDay])

  useEffect(() => {
    if (disabled || readOnly) setOpen(false)
  }, [disabled, readOnly])

  const displayDate = parseDate(value)
  const display = displayDate
    ? `${displayDate.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })}${withTime && value.includes('T') ? `, ${value.split('T')[1].slice(0, 5)}` : ''}`
    : ''
  return (
    <CalendarContext.Provider value={popupId}>
      <span className={cn('date-picker', compact && 'date-picker-compact', className)}>
        {name && <input type="hidden" name={name} value={value} disabled={disabled} />}
        <button
          ref={triggerRef}
          id={id}
          type="button"
          className={cn('date-picker-trigger', !display && 'is-empty')}
          disabled={disabled || readOnly}
          aria-label={label}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls={open ? popupId : undefined}
          aria-required={required}
          onClick={() => {
            if (open) return close()
            const initial = parseDate(value) ?? new Date()
            setDraft(value)
            focusDate(initial)
            setOpen(true)
          }}
        >
          <CalendarDays size={17} aria-hidden="true" />
          <span>{display || placeholder || (withTime ? 'Select date & time' : 'Select date')}</span>
        </button>
        {open &&
          createPortal(
            <div
              ref={popupRef}
              id={popupId}
              role="dialog"
              aria-label={`${label} calendar`}
              className="date-picker-popup"
              style={position}
              onKeyDown={(event) => {
                if (event.key === 'Escape') {
                  event.preventDefault()
                  event.stopPropagation()
                  close()
                }
                if (event.key === 'Tab') {
                  const focusable = [
                    ...(popupRef.current?.querySelectorAll<HTMLElement>(
                      'button:not(:disabled), select:not(:disabled)',
                    ) ?? []),
                  ].filter((element) => element.tabIndex >= 0)
                  const first = focusable[0]
                  const last = focusable[focusable.length - 1]
                  if (event.shiftKey && document.activeElement === first) {
                    event.preventDefault()
                    last?.focus()
                  } else if (!event.shiftKey && document.activeElement === last) {
                    event.preventDefault()
                    first?.focus()
                  }
                }
              }}
            >
              <div className="date-picker-caption">
                <span>
                  <CalendarDays size={14} /> {label}
                </span>
                <button type="button" aria-label="Close calendar" onClick={() => close()}>
                  <X size={15} />
                </button>
              </div>
              <div className="date-picker-navigation">
                <button
                  type="button"
                  aria-label="Previous month"
                  onClick={() => focusDate(shiftMonth(visibleMonth, -1))}
                >
                  <ChevronLeft size={17} />
                </button>
                <div>
                  <Select
                    compact
                    aria-label="Calendar month"
                    value={visibleMonth.getMonth()}
                    onChange={(event) =>
                      focusDate(new Date(visibleMonth.getFullYear(), Number(event.target.value), 1))
                    }
                  >
                    {MONTHS.map((month, index) => (
                      <option key={month} value={index}>
                        {month}
                      </option>
                    ))}
                  </Select>
                  <Select
                    compact
                    aria-label="Calendar year"
                    value={visibleMonth.getFullYear()}
                    onChange={(event) =>
                      focusDate(new Date(Number(event.target.value), visibleMonth.getMonth(), 1))
                    }
                  >
                    {years.map((year) => (
                      <option key={year} value={year}>
                        {year}
                      </option>
                    ))}
                  </Select>
                </div>
                <button
                  type="button"
                  aria-label="Next month"
                  onClick={() => focusDate(shiftMonth(visibleMonth, 1))}
                >
                  <ChevronRight size={17} />
                </button>
              </div>
              <div
                role="grid"
                aria-label={`${MONTHS[visibleMonth.getMonth()]} ${visibleMonth.getFullYear()}`}
                className="date-picker-grid"
              >
                <div role="row" className="date-picker-weekdays">
                  {WEEKDAYS.map((day) => (
                    <span role="columnheader" key={day}>
                      {day}
                    </span>
                  ))}
                </div>
                {Array.from({ length: 6 }, (_, rowIndex) => (
                  <div role="row" className="date-picker-week" key={rowIndex}>
                    {days.slice(rowIndex * 7, rowIndex * 7 + 7).map((date) => {
                      const key = dateKey(date)
                      const isSelected = selectedDate && key === dateKey(selectedDate)
                      return (
                        <span role="gridcell" aria-selected={Boolean(isSelected)} key={key}>
                          <button
                            type="button"
                            ref={(element) => {
                              dayRefs.current[key] = element
                            }}
                            tabIndex={key === focusedDay ? 0 : -1}
                            aria-label={date.toLocaleDateString('en-GB', {
                              weekday: 'long',
                              day: 'numeric',
                              month: 'long',
                              year: 'numeric',
                            })}
                            aria-current={key === today ? 'date' : undefined}
                            disabled={!allowed(key)}
                            className={cn(
                              'date-picker-day',
                              date.getMonth() !== visibleMonth.getMonth() && 'is-outside',
                              isSelected && 'is-selected',
                              key === today && 'is-today',
                            )}
                            onClick={() => chooseDay(date)}
                            onKeyDown={(event) => handleKeys(event, date)}
                          >
                            {date.getDate()}
                          </button>
                        </span>
                      )
                    })}
                  </div>
                ))}
              </div>
              {withTime && (
                <div className="date-picker-time">
                  <span>
                    <Clock3 size={15} /> Time
                  </span>
                  <div>
                    <Select
                      compact
                      aria-label="Hour"
                      value={time.split(':')[0]}
                      onChange={(event) =>
                        setDraft(
                          `${draft.slice(0, 10) || today}T${event.target.value}:${time.split(':')[1]}`,
                        )
                      }
                    >
                      {Array.from({ length: 24 }, (_, index) => String(index).padStart(2, '0')).map(
                        (hour) => (
                          <option key={hour}>{hour}</option>
                        ),
                      )}
                    </Select>
                    <span>:</span>
                    <Select
                      compact
                      aria-label="Minute"
                      value={time.split(':')[1]}
                      onChange={(event) =>
                        setDraft(
                          `${draft.slice(0, 10) || today}T${time.split(':')[0]}:${event.target.value}`,
                        )
                      }
                    >
                      {Array.from({ length: 60 }, (_, index) => String(index).padStart(2, '0')).map(
                        (minute) => (
                          <option key={minute}>{minute}</option>
                        ),
                      )}
                    </Select>
                  </div>
                </div>
              )}
              <footer className="date-picker-footer">
                <button
                  type="button"
                  onClick={() => {
                    const now = new Date()
                    if (withTime) {
                      setDraft(
                        `${dateKey(now)}T${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`,
                      )
                      focusDate(now)
                    } else if (allowed(today)) commit(today)
                  }}
                  disabled={!allowed(today)}
                >
                  Today
                </button>
                <div>
                  {!required && (
                    <button type="button" onClick={() => commit('')}>
                      Clear
                    </button>
                  )}
                  {withTime && (
                    <button
                      type="button"
                      className="date-picker-apply"
                      disabled={
                        !selectedDate ||
                        !allowed(draft.slice(0, 10)) ||
                        Boolean(min && draft < min) ||
                        Boolean(max && draft > max)
                      }
                      onClick={() => commit(draft)}
                    >
                      Apply
                    </button>
                  )}
                </div>
              </footer>
            </div>,
            document.body,
          )}
      </span>
    </CalendarContext.Provider>
  )
}
