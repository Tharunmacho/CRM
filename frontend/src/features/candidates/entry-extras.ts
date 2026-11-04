import type { CandidateEntryForm } from './entry-model'
import { phoneDigits } from './entry-phone'

/** "WhatsApp is the same as the mobile number" holds while the switch is on and no other WhatsApp number was read or typed. */
export function whatsappIsSame(form: Pick<CandidateEntryForm, 'whatsappSame' | 'whatsapp' | 'phone'>): boolean {
  if (form.whatsappSame !== 'yes') return false
  const own = phoneDigits(form.whatsapp)
  const primary = phoneDigits(form.phone)
  // The same number with or without the country code (a document often prints it without).
  return !own || own === primary || (own.length >= 7 && primary.length >= 7 && (primary.endsWith(own) || own.endsWith(primary)))
}

/** The WhatsApp number to save: the mobile number while "same as primary" holds, otherwise the box. */
export function effectiveWhatsapp(form: Pick<CandidateEntryForm, 'whatsappSame' | 'whatsapp' | 'phone'>): string {
  return whatsappIsSame(form) ? form.phone.trim() : form.whatsapp.trim()
}

/** The native place to save: the current city while "same as current" is on. */
export function effectiveNativePlace(form: Pick<CandidateEntryForm, 'nativeSame' | 'nativePlace' | 'currentCity'>): string {
  return form.nativeSame === 'yes' ? form.currentCity.trim() : form.nativePlace.trim()
}

/** The screening keys of the candidate-create body, empty ones left out. */
export function screeningPayload(form: CandidateEntryForm) {
  const text = (value: string) => value.trim() || undefined
  return {
    native_place: text(effectiveNativePlace(form)),
    family_member_number: text(form.familyNumber),
    country_preference: text(form.countryPreference),
    job_duration_preference: text(form.jobDurationPreference),
    best_time_to_contact: text(form.bestTimeToContact),
    service_charges_status: form.serviceChargesStatus || 'not_discussed',
    // The list of levels is managed in Agency settings; the server checks the value against it.
    interest_level: form.interestLevel.trim() || undefined,
    next_follow_up_date: form.nextFollowUp || undefined,
  }
}
