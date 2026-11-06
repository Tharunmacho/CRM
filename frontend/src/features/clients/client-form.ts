/**
 * Pure helpers behind the client and reference agency dialogs: staff-row cleaning, validation, request bodies and
 * server error mapping. Kept free of React and of imports so they can be unit tested on their own.
 */
import type { StaffContact } from '../../shared/model/types'

/** The six details both a client and a reference agency carry. `name` is the contact person. */
export interface PartyFields {
  name: string
  agencyName: string
  phone: string
  email: string
  address: string
  state: string
}

/** One row of the "Staff details" group as typed (every value is a string while editing). */
export interface StaffRow { name: string; designation: string; phone: string; email: string }

export type FieldErrors = Record<string, string>

/** The server keeps at most this many staff entries per record (client-agency-contract.md). */
export const MAX_STAFF_ROWS = 50

export const emptyStaffRow = (): StaffRow => ({ name: '', designation: '', phone: '', email: '' })

export const emptyParty = (): PartyFields => ({ name: '', agencyName: '', phone: '', email: '', address: '', state: '' })


const trim = (value: string | undefined | null) => (value ?? '').trim()

/** A row with nothing typed in any box is ignored on save, so a spare "+ Add staff" row never blocks the form. */
export const isEmptyStaffRow = (row: StaffRow) => !trim(row.name) && !trim(row.designation) && !trim(row.phone) && !trim(row.email)

/** The rows to show when editing: the saved staff, or nothing (the form starts without a blank row). */
export function staffRowsOf(contacts?: StaffContact[] | null): StaffRow[] {
  return (contacts ?? []).map((contact) => ({ name: contact.name ?? '', designation: contact.designation ?? '', phone: contact.phone ?? '', email: contact.email ?? '' }))
}

/** Trimmed staff entries for the server: completely empty rows are dropped and blank optional values are left out. */
export function cleanStaffRows(rows: StaffRow[]): StaffContact[] {
  return rows.filter((row) => !isEmptyStaffRow(row)).map((row) => {
    const contact: StaffContact = { name: trim(row.name) }
    if (trim(row.designation)) contact.designation = trim(row.designation)
    if (trim(row.phone)) contact.phone = trim(row.phone)
    if (trim(row.email)) contact.email = trim(row.email)
    return contact
  })
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const PHONE_CHARS = /^[0-9+\-().\s/]+$/

/** Plain-English message for an email box, or '' when it is blank or fine. */
export function emailProblem(value: string): string {
  const text = trim(value)
  if (!text) return ''
  return EMAIL.test(text) ? '' : 'Enter a valid email address, for example name@company.com.'
}

/** Plain-English message for a phone box, or '' when it is blank or fine. */
export function phoneProblem(value: string): string {
  const text = trim(value)
  if (!text) return ''
  if (!PHONE_CHARS.test(text)) return 'Use digits only, with an optional + at the start. Spaces and dashes are fine.'
  const digits = text.replace(/\D/g, '').length
  return digits < 5 || digits > 15 ? 'A phone number has between 5 and 15 digits.' : ''
}

/**
 * Field-level problems keyed by field name; staff rows use `staff.<index>.<field>` so the message shows on the right box.
 * An empty object means the form can be sent.
 */
export function validateParty(fields: PartyFields, rows: StaffRow[]): FieldErrors {
  const errors: FieldErrors = {}
  if (!trim(fields.name)) errors.name = 'Enter the contact person’s name.'
  if (!trim(fields.agencyName)) errors.agencyName = 'Enter the name of the agency.'
  const phone = phoneProblem(fields.phone); if (phone) errors.phone = phone
  const email = emailProblem(fields.email); if (email) errors.email = email
  if (rows.filter((row) => !isEmptyStaffRow(row)).length > MAX_STAFF_ROWS) errors.staff = `You can add up to ${MAX_STAFF_ROWS} staff.`
  rows.forEach((row, index) => {
    if (isEmptyStaffRow(row)) return
    if (!trim(row.name)) errors[`staff.${index}.name`] = 'Enter this person’s name, or remove the row.'
    const staffPhone = phoneProblem(row.phone); if (staffPhone) errors[`staff.${index}.phone`] = staffPhone
    const staffEmail = emailProblem(row.email); if (staffEmail) errors[`staff.${index}.email`] = staffEmail
  })
  return errors
}

const optional = (value: string) => trim(value) || undefined

/** Body for POST /reference-agencies and PUT /reference-agencies/:id. */
export function buildAgencyPayload(fields: PartyFields, rows: StaffRow[]) {
  return {
    name: trim(fields.name),
    agencyName: trim(fields.agencyName),
    phone: optional(fields.phone),
    email: optional(fields.email),
    address: optional(fields.address),
    state: optional(fields.state),
    staffContacts: cleanStaffRows(rows),
  }
}

/** The names the form uses for the server's field names (`agency_name`, `contactEmail` ...). */
const FIELD_ALIASES: Record<string, string> = {
  agency_name: 'agencyName', agencyname: 'agencyName', contactemail: 'email', contact_email: 'email', contactperson: 'name', contact_person: 'name',
  staff_contacts: 'staff', staffcontacts: 'staff',
}
const FORM_FIELDS = new Set(['name', 'agencyName', 'phone', 'email', 'address', 'state', 'staff', 'staffIds'])

/**
 * Turns a 422 body (`[{loc: ['body', 'staffContacts', 0, 'email'], msg}]`) into field errors, so each message shows under
 * its box. Anything that does not belong to a known box comes back in `general`.
 */
export function fieldErrorsFromApi(detail: unknown): { fields: FieldErrors; general: string } {
  const fields: FieldErrors = {}
  const general: string[] = []
  if (!Array.isArray(detail)) return { fields, general: typeof detail === 'string' ? detail : '' }
  for (const item of detail) {
    const message = typeof item?.msg === 'string' ? item.msg : ''
    if (!message) continue
    const loc: unknown[] = Array.isArray(item.loc) ? item.loc.filter((part: unknown) => part !== 'body' && part !== 'company') : []
    const head = typeof loc[0] === 'string' ? (FIELD_ALIASES[loc[0].toLowerCase()] ?? loc[0]) : ''
    let key = ''
    if (head === 'staff' && typeof loc[1] === 'number' && typeof loc[2] === 'string') key = `staff.${loc[1]}.${loc[2]}`
    else if (FORM_FIELDS.has(head)) key = head
    if (key && !fields[key]) fields[key] = sentence(message)
    else if (!key) general.push((head ? head + ': ' : '') + sentence(message))
  }
  return { fields, general: general.join(' ') }
}

function sentence(message: string) {
  const text = message.trim()
  return /[.!?]$/.test(text) ? text : text + '.'
}

/** First word of a person's name, for the small staff chips ("Anita Rao" -> "Anita"). */
export function firstName(name: string | undefined | null): string {
  return trim(name).split(/\s+/)[0] ?? ''
}
