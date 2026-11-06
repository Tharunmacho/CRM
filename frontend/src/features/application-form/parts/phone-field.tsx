import { Input, Select } from '@/shared/ui/primitives'
import { COUNTRIES, DEFAULT_CODE, joinPhone, phoneHint, splitPhone } from './phone'

/**
 * Phone input with a country-code select. Props: `value` (the whole number, e.g. "+91 98765 43210"),
 * `onChange(whole)`, `label` (aria label), `error?`, `id?`. Shows "Selected: India (+91) - Requires 10 digits" underneath.
 * Wrap it in a labelled `GridField`; it renders no visible label of its own.
 */
export function PhoneField({ value, onChange, label, error, id, compact }: { value: string; onChange: (next: string) => void; label: string; error?: string; id?: string; compact?: boolean }) {
  const { code, national } = splitPhone(value, DEFAULT_CODE)
  return (
    <div className="afp-phone">
      <div className="afp-phone-row">
        <Select value={code} aria-label={`${label} country code`} onChange={(event) => onChange(joinPhone(event.target.value, national))}>
          {COUNTRIES.map((country) => <option key={country.code} value={country.code}>{compact ? country.code : `${country.code} ${country.name}`}</option>)}
        </Select>
        <Input id={id} type="tel" inputMode="tel" value={national} aria-label={label} placeholder="Phone number" onChange={(event) => onChange(joinPhone(code, event.target.value))} />
      </div>
      {error ? <p className="afp-hint is-error" role="alert">{error}</p> : <p className="afp-hint">{phoneHint(code)}</p>}
    </div>
  )
}
