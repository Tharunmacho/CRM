import {
  forwardRef,
  useEffect,
  useRef,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type TextareaHTMLAttributes,
} from 'react'
import { cn } from '@/shared/lib/cn'
import { FieldLabelContext } from './control-context'
export { Select } from './select'

/* ---------------------------------------------------------------- Button */

type Variant = 'primary' | 'accent' | 'secondary' | 'ghost' | 'danger' | 'dangerGhost'
type Size = 'sm' | 'md'

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-navy-600 text-white hover:bg-navy-700 disabled:bg-ink-300',
  accent: 'bg-navy-600 font-semibold text-white hover:bg-navy-700 disabled:bg-ink-300',
  secondary:
    'bg-white text-ink border border-rule hover:bg-field hover:border-ink-300 disabled:text-ink-300',
  ghost: 'text-ink-600 hover:bg-field hover:text-ink disabled:text-ink-300',
  danger: 'bg-stamp-600 text-white hover:bg-stamp-700 disabled:bg-ink-300',
  dangerGhost: 'text-stamp-600 hover:bg-stamp-50 border border-transparent hover:border-stamp-100',
}

const SIZES: Record<Size, string> = {
  sm: 'h-8 px-3 text-xs gap-1.5',
  md: 'h-10 px-4 text-sm gap-2',
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  icon?: ReactNode
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', icon, className, children, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      className={cn(
        'ui-button inline-flex items-center justify-center rounded-control font-medium transition-colors',
        `ui-button-${variant}`,
        'disabled:cursor-not-allowed',
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    >
      {icon}
      {children}
    </button>
  )
})

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string
  tone?: 'default' | 'danger'
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, tone = 'default', className, children, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      aria-label={label}
      title={label}
      className={cn(
        'inline-flex h-8 w-8 items-center justify-center rounded-control transition-colors',
        tone === 'danger'
          ? 'text-stamp-600 hover:bg-stamp-50'
          : 'text-ink-400 hover:bg-field hover:text-ink',
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  )
})

/* ----------------------------------------------------------------- Field */

export function Field({
  label,
  hint,
  error,
  required,
  children,
  className,
}: {
  label?: string
  hint?: string
  error?: string
  required?: boolean
  children: ReactNode
  className?: string
}) {
  return (
    <FieldLabelContext.Provider value={label}>
      <label className={cn('ui-field block', className)}>
        {label && (
          <span className="mb-1.5 flex items-baseline gap-1 text-xs font-medium text-ink-600">
            {label}
            {required && <span className="text-stamp-600">*</span>}
          </span>
        )}
        {children}
        {hint && !error && <span className="mt-1 block text-micro text-ink-400">{hint}</span>}
        {error && <span className="mt-1 block text-micro text-stamp-600">{error}</span>}
      </label>
    </FieldLabelContext.Provider>
  )
}

const inputBase =
  'ui-input w-full rounded-control border border-rule bg-white px-3 text-sm text-ink transition-colors placeholder:text-ink-300 hover:border-ink-300 focus:border-navy-500 disabled:bg-field disabled:text-ink-400'

export const Input = forwardRef<
  HTMLInputElement,
  InputHTMLAttributes<HTMLInputElement> & { ident?: boolean }
>(function Input({ className, ident, ...rest }, ref) {
  return (
    <input ref={ref} className={cn(inputBase, 'h-10', ident && 'ident', className)} {...rest} />
  )
})

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className, rows = 3, ...rest }, ref) {
  return (
    <textarea
      ref={ref}
      rows={rows}
      className={cn(inputBase, 'py-2.5 leading-relaxed', className)}
      {...rest}
    />
  )
})

interface CheckboxInputProps extends Omit<
  InputHTMLAttributes<HTMLInputElement>,
  'type' | 'checked' | 'defaultChecked' | 'onChange'
> {
  checked: boolean
  indeterminate?: boolean
  onChange: (checked: boolean) => void
}

/**
 * The box on its own, for rows that are already a label or a button — nesting the
 * full Checkbox (which is a <label>) inside one is invalid markup.
 */
export function CheckboxInput({
  checked,
  indeterminate,
  onChange,
  disabled,
  className,
  ...rest
}: CheckboxInputProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (inputRef.current) inputRef.current.indeterminate = Boolean(indeterminate)
  }, [indeterminate])
  return (
    <input
      {...rest}
      ref={inputRef}
      type="checkbox"
      checked={checked}
      disabled={disabled}
      aria-checked={indeterminate ? 'mixed' : checked}
      onChange={(e) => onChange(e.target.checked)}
      className={cn('custom-checkbox', className)}
    />
  )
}

export function Checkbox({
  checked,
  onChange,
  label,
  disabled,
  className,
  ...rest
}: CheckboxInputProps & { label?: ReactNode }) {
  return (
    <label
      className={cn(
        'inline-flex cursor-pointer items-start gap-2 text-sm',
        disabled && 'cursor-not-allowed opacity-50',
      )}
    >
      <CheckboxInput
        {...rest}
        checked={checked}
        disabled={disabled}
        onChange={onChange}
        className={cn('mt-0.5', className)}
      />
      {label && <span className="text-ink-700">{label}</span>}
    </label>
  )
}

export function RadioRow({
  name,
  value,
  options,
  onChange,
  disabled,
}: {
  name: string
  value?: string
  options: { value: string; label: string }[]
  onChange: (v: string) => void
  disabled?: boolean
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((o) => {
        const active = value === o.value
        return (
          <button
            key={o.value}
            type="button"
            disabled={disabled}
            aria-pressed={active}
            onClick={() => onChange(o.value)}
            className={cn(
              'h-10 rounded-control border px-4 text-sm font-medium transition-colors',
              active
                ? 'border-navy-600 bg-navy-50 text-navy-700'
                : 'border-rule bg-white text-ink-600 hover:border-ink-300 hover:text-ink',
              disabled && 'cursor-not-allowed opacity-60',
            )}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

/**
 * Toggle. The knob is laid out by flexbox inside a padded track rather than nudged
 * with hand-tuned offsets, so it stays centred at any size and never drifts into the
 * label. Sized to sit on the same baseline as a line of body text.
 */
export function Switch({
  checked,
  onChange,
  label,
  description,
  disabled,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label?: string
  description?: string
  disabled?: boolean
}) {
  const control = (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        'inline-flex h-6 w-11 shrink-0 items-center rounded-full p-0.5 transition-colors',
        checked ? 'bg-navy-600' : 'bg-ink-300',
        disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer',
      )}
    >
      <span
        className={cn(
          'h-5 w-5 rounded-full bg-white shadow-sm transition-transform duration-150',
          checked ? 'translate-x-5' : 'translate-x-0',
        )}
      />
    </button>
  )

  if (!label) return control

  return (
    <label
      className={cn(
        'inline-flex items-start gap-3',
        disabled ? 'cursor-not-allowed' : 'cursor-pointer',
      )}
    >
      {control}
      <span className="min-w-0">
        <span className={cn('block text-sm leading-6', disabled ? 'text-ink-400' : 'text-ink-700')}>
          {label}
        </span>
        {description && <span className="mt-0.5 block text-micro text-ink-400">{description}</span>}
      </span>
    </label>
  )
}

/* ------------------------------------------------------------- Searchbox */

export function SearchInput({
  value,
  onChange,
  placeholder = 'Search',
  className,
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  className?: string
}) {
  return (
    <div className={cn('ui-search relative', className)}>
      <svg
        className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-300"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
      >
        <circle cx="11" cy="11" r="7" />
        <path d="m20 20-3.5-3.5" strokeLinecap="round" />
      </svg>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        className={cn(inputBase, 'h-10 pl-9')}
      />
    </div>
  )
}
