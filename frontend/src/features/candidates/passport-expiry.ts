export interface ExpiryNotice {
  tone: 'expired' | 'soon'
  text: string
  days: number
}

/** Most Gulf destinations want six months of validity left on arrival. */
export const PASSPORT_WARN_DAYS = 180

/**
 * How long a passport has left, as a recruiter needs to hear it: a passport that is expired or
 * runs out inside the deployment window stops an application. Null when the date is missing,
 * unreadable, or comfortably far away.
 */
export function passportExpiryNotice(value?: string | null, now: Date = new Date()): ExpiryNotice | null {
  if (!value) return null
  const expiry = new Date(value)
  if (Number.isNaN(expiry.getTime())) return null
  const days = Math.ceil((expiry.getTime() - now.getTime()) / 86_400_000)
  if (days <= 0) return { tone: 'expired', text: 'Passport expired', days }
  if (days <= PASSPORT_WARN_DAYS) return { tone: 'soon', text: `Passport expires in ${days} ${days === 1 ? 'day' : 'days'}`, days }
  return null
}
