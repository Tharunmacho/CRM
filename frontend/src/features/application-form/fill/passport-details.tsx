import { AlertTriangle, CheckCircle2 } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import type { FormContext } from '../components/sections'
import { FillFieldView, useFillEnv } from './fill-controls'
import { PASSPORT_FILL_FIELDS, PASSPORT_STATUS_TEXT, passportExpiryHint, passportStatus } from './fill-fields'

/** "Passport valid" / "Expiring soon" / "Passport expired", from the date of expiry in the draft; nothing without a date. */
export function PassportStatusChip({ expiryDate }: { expiryDate: string }) {
  const status = passportStatus(expiryDate)
  const label = PASSPORT_STATUS_TEXT[status]
  if (!label) return null
  return (
    <span className={cn('aff-status', `is-${status}`)} role="status">
      {status === 'valid' ? <CheckCircle2 aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}
      {label}
    </span>
  )
}

/**
 * The four passport details (number, place of issue, date of issue, date of expiry) on the shared 12-column grid, as the
 * body of section 2 (Passport details). No header of its own: the section header carries the title, the expiry status and
 * the upload button. They are typed straight into the draft, so the paper view and the completeness follow as the person
 * types; read only when the person cannot work on the candidate. The field wrappers keep their `data-field` keys
 * (`passport.passportNumber` ...), so "Fill in" on a missing passport field lands here.
 */
export function PassportDetailsFields({ ctx, canWork }: { ctx: FormContext; canWork: boolean }) {
  const env = useFillEnv(ctx)
  const expiry = ctx.draft.passport.expiryDate
  return (
    <div className="aff aff-embedded" id="af-passport-details" role="group" aria-label="Passport details">
      <div className="aff-grid">
        {PASSPORT_FILL_FIELDS.map((field) => (
          <FillFieldView key={field.key} env={env} field={field} canWork={canWork} hint={field.key === 'passport.expiryDate' ? passportExpiryHint(expiry) || undefined : undefined} />
        ))}
      </div>
    </div>
  )
}
