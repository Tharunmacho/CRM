import { cn } from '@/shared/lib/cn'
import { INTEREST_LEVELS, type InterestLevel } from './interest'
import { toneStyle } from '@/features/form-options/form-options-model'
import type { FormOption } from '@/features/form-options/form-options-model'

/**
 * Four coloured, selectable cards: More interested (green), Interested (blue), Somewhat interested (amber),
 * Not interested (red). Props: `value` ('' for none), `onChange(level)`, `disabled?`. Click the chosen card again to clear.
 */
export function InterestCards({ value, onChange, disabled, levels }: { value: InterestLevel | string; onChange: (next: InterestLevel | '') => void; disabled?: boolean; levels?: FormOption[] }) {
  // `levels` are the admin-managed interest options (label, hint and tone come from the database); the built-in four are the fallback.
  const rows = (levels && levels.length ? levels.map((option) => ({ value: option.value, label: option.label, hint: option.hint ?? '', tone: toneStyle(option.tone).name ?? 'gray', color: toneStyle(option.tone).color })) : INTEREST_LEVELS.map((level) => ({ ...level, color: undefined as string | undefined })))
  return (
    <div className="afp-interest" role="radiogroup" aria-label="Interest level">
      {rows.map((level) => (
        <button
          key={level.value}
          type="button"
          role="radio"
          aria-checked={value === level.value}
          disabled={disabled}
          className={cn('afp-interest-card', `is-${level.tone}`, value === level.value && 'is-on')}
          style={level.color ? ({ ['--tone' as string]: level.color } as React.CSSProperties) : undefined}
          onClick={() => onChange(value === level.value ? '' : (level.value as InterestLevel))}
        >
          <i aria-hidden="true" />
          <b>{level.label}</b>
          {level.hint && <small>{level.hint}</small>}
        </button>
      ))}
    </div>
  )
}
