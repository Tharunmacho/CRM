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

/* Variant colours are token based (--primary, --on-primary, --tint-*, --error-*), so the primary button
   follows the palette and dark mode. They are utilities, so a className on one button still overrides. */
const PRIMARY =
  'border-[color:var(--primary)] bg-[color:var(--primary)] text-[color:var(--on-primary)] hover:border-[color:var(--primary-hover)] hover:bg-[color:var(--primary-hover)] disabled:border-[color:var(--tint-3)] disabled:bg-[color:var(--tint-3)] disabled:text-[color:var(--text-light)]'
const VARIANTS: Record<Variant, string> = {
  primary: PRIMARY,
  accent: PRIMARY,
  secondary:
    'border-[color:var(--border-control)] bg-[color:var(--bg-card)] text-[color:var(--text-main)] hover:border-[color:var(--border-hover)] hover:bg-[color:var(--tint-1)] disabled:bg-[color:var(--tint-1)] disabled:text-[color:var(--text-light)]',
  ghost:
    'border-transparent bg-transparent text-[color:var(--text-muted)] hover:bg-[color:var(--tint-2)] hover:text-[color:var(--text-main)] disabled:text-[color:var(--text-light)]',
  danger:
    'border-[color:var(--error-ink)] bg-[color:var(--error-ink)] text-[color:var(--bg-card)] hover:opacity-90 disabled:border-[color:var(--tint-3)] disabled:bg-[color:var(--tint-3)] disabled:text-[color:var(--text-light)]',
  dangerGhost:
    'border-transparent bg-transparent text-[color:var(--error-ink)] hover:border-[color:var(--error-edge)] hover:bg-[color:var(--error-fill)]',
}

const SIZES: Record<Size, string> = {
  sm: 'h-8 px-3 text-[13px] gap-1.5',
  md: 'h-10 px-4 text-[14px] gap-2',
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
      type="button"
      className={cn(
        'ui-button inline-flex shrink-0 items-center justify-center whitespace-nowrap rounded-[8px] border font-medium transition-colors [&>svg]:h-4 [&>svg]:w-4 [&>svg]:shrink-0',
        `ui-button-${variant}`,
        VARIANTS[variant],
        'disabled:cursor-not-allowed',
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
      type="button"
      aria-label={label}
      title={label}
      className={cn(
        'ui-icon-button inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-control transition-colors',
        tone === 'danger'
          ? 'text-stamp-600 hover:bg-stamp-50'
          : 'text-ink-500 hover:bg-field hover:text-ink',
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
          <span className="mb-1.5 flex items-baseline gap-1 text-[13px] font-medium text-ink-700">
            {label}
            {required && <span className="text-stamp-600">*</span>}
          </span>
        )}
        {children}
        {hint && !error && <span className="mt-1.5 block text-[12.5px] leading-5 text-ink-500">{hint}</span>}
        {error && <span className="mt-1.5 block text-[12.5px] leading-5 text-stamp-600">{error}</span>}
      </label>
    </FieldLabelContext.Provider>
  )
}

/* Border, hover, focus and disabled colours live in styles/depth.css (.ui-input), token based. */
const inputBase = 'ui-input w-full rounded-[8px] border bg-white px-3 text-[14px] text-ink transition-colors'

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
      className={cn(inputBase, 'min-h-[88px] py-2.5 leading-relaxed', className)}
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
        'inline-flex cursor-pointer items-start gap-2 text-[14px]',
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
    <div className="flex flex-wrap gap-1.5" data-name={name}>
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
              'ui-radio-chip h-10 rounded-[8px] border px-4 text-[14px] font-medium transition-colors',
              active && 'is-on',
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
  compact = false,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label?: string
  description?: string
  disabled?: boolean
  compact?: boolean
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
        'ui-switch inline-flex h-6 w-11 shrink-0 items-center rounded-full p-0.5 transition-colors',
        checked ? 'bg-navy-600' : 'bg-ink-300',
        disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer',
      )}
    >
      <span
        className={cn(
          'ui-switch-knob h-5 w-5 rounded-full bg-white transition-transform duration-150',
          checked ? 'translate-x-5' : 'translate-x-0',
        )}
      />
    </button>
  )

  if (!label || compact) return control

  return (
    <label
      className={cn(
        'inline-flex items-start gap-3',
        disabled ? 'cursor-not-allowed' : 'cursor-pointer',
      )}
    >
      {control}
      <span className="min-w-0">
        <span className={cn('block text-[14px] leading-6', disabled ? 'text-ink-400' : 'text-ink-700')}>
          {label}
        </span>
        {description && <span className="mt-0.5 block text-[12.5px] text-ink-500">{description}</span>}
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
        className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400"
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
        className={cn(inputBase, 'h-10 pl-10')}
      />
    </div>
  )
}
