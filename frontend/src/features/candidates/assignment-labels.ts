/**
 * The words around choosing a recruiter. A candidate is never given to anyone automatically: an admin or a manager picks a
 * recruiter (or leaves the candidate unassigned), and a staff member who adds a candidate by hand owns it. Plain text in,
 * plain text out, so the labels are tested without a browser.
 */
import type { AgencySettings } from '@/shared/model/types'

/** What an empty recruiter reads as, everywhere a candidate's recruiter is shown. Muted, never a name. */
export const NOT_ASSIGNED = 'Not assigned'

/** The first option of the Recruiter dropdown on the Add candidate screen (admin and manager). */
export const NOT_ASSIGNED_YET = 'Not assigned yet'

/** The recruiter's name for an owner that may be missing. */
export const recruiterText = (name?: string | null): string => name?.trim() || NOT_ASSIGNED

/** True when there is no recruiter to name, so the screen mutes the text. */
export const isUnassigned = (name?: string | null): boolean => !name?.trim()

const firstName = (name?: string | null): string => name?.trim().split(/\s+/)[0] ?? ''

export interface RecruiterChoice { id: string; name: string; role: string; active?: boolean; onLeave?: boolean }

/**
 * The people an admin or a manager can hand a new candidate to: active staff and managers, A to Z. Someone on leave is still
 * listed (a deliberate manual hand-over is allowed), marked in the label.
 */
export function recruiterChoices(users: readonly RecruiterChoice[]): { id: string; label: string }[] {
  return users
    .filter((user) => user.active !== false && (user.role === 'staff' || user.role === 'manager'))
    .map((user) => ({ id: user.id, label: `${user.name}${user.role === 'manager' ? ' (manager)' : ''}${user.onLeave ? ' · on leave' : ''}`, name: user.name }))
    .sort((a, b) => a.name.localeCompare(b.name))
    .map(({ id, label }) => ({ id, label }))
}

/** The save button. "Save and assign to <first name>" only once a recruiter is chosen. */
export function saveButtonLabel(picksRecruiter: boolean, recruiterName?: string | null): string {
  const first = picksRecruiter ? firstName(recruiterName) : ''
  return first ? `Save and assign to ${first}` : 'Save candidate'
}

/** The one short sentence under the button. */
export function savebarStatus(picksRecruiter: boolean, recruiterName?: string | null): string {
  if (!picksRecruiter) return 'You will own this candidate.'
  const name = recruiterName?.trim()
  return name ? `${name} will own this candidate.` : 'No recruiter is chosen. An admin can assign one later.'
}

/** The hint under the Recruiter field (admin and manager only; a staff member does not see the field). */
export const RECRUITER_FIELD_HINT = 'Optional. Leave on Not assigned to choose later.'

/** The hint under the follow-up date: it only reaches a to-do list once somebody owns the candidate. */
export function followUpHint(picksRecruiter: boolean, recruiterName?: string | null): string {
  if (!picksRecruiter) return 'Adds a follow-up to your to-do list.'
  return recruiterName?.trim() ? 'Adds a follow-up to the recruiter’s to-do list.' : 'Kept on the candidate. It reaches a to-do list once a recruiter is assigned.'
}

/** The third step of the page's progress list. */
export function saveStep(picksRecruiter: boolean, recruiterName?: string | null): { label: string; detail: string } {
  const label = saveButtonLabel(picksRecruiter, recruiterName)
  if (!picksRecruiter) return { label, detail: 'You own this candidate and have 48 hours to make the initial call.' }
  return recruiterName?.trim()
    ? { label, detail: `${recruiterName.trim()} gets 48 hours to make the initial call.` }
    : { label, detail: 'No recruiter is chosen. An admin can assign one later, and the 48 hours start then.' }
}

/** The message after saving. */
export function savedNotice(picksRecruiter: boolean, ownerName: string | null): { text: string; tone?: 'warn' } {
  if (ownerName) return { text: `Saved. ${picksRecruiter ? `Assigned to ${ownerName}` : 'Assigned to you'}. The initial call is due within 48 hours.` }
  return { text: 'Saved. No recruiter is assigned yet. An admin or a manager can assign one.', tone: 'warn' }
}

/** The note under the recruiter on the saved result card. */
export function savedOwnerNote(picksRecruiter: boolean, ownerName: string | null, merged: boolean): string {
  if (!ownerName) return 'No recruiter is assigned yet. An admin or a manager can assign one.'
  if (merged) return 'Combining does not change who owns this candidate.'
  return picksRecruiter ? 'The recruiter you picked.' : 'A candidate you add is yours.'
}

/** The setting the server reports; undefined on a server that does not have it yet. */
export function autoAssignValue(settings: Pick<AgencySettings, 'autoAssign'> | null | undefined): boolean | undefined {
  return typeof settings?.autoAssign === 'boolean' ? settings.autoAssign : undefined
}

export interface AutoAssignToggle {
  checked: boolean
  /** True when the server is too old to hold the setting, or the person may not change it. */
  disabled: boolean
  label: string
  helper: string
  /** Shown beside a disabled switch: why it cannot be used. */
  note: string
}

export const AUTO_ASSIGN_LABEL = 'Assign new candidates automatically'
export const AUTO_ASSIGN_HELPER = 'When off, new candidates wait for you to assign a recruiter.'

export function autoAssignToggle(settings: Pick<AgencySettings, 'autoAssign' | 'editable'> | null | undefined, isAdmin: boolean): AutoAssignToggle {
  const value = autoAssignValue(settings)
  const supported = value !== undefined
  return {
    checked: value === true,
    disabled: !supported || !isAdmin || settings?.editable === false,
    label: AUTO_ASSIGN_LABEL,
    helper: AUTO_ASSIGN_HELPER,
    note: !supported ? 'Needs the latest server' : !isAdmin ? 'Only an admin can change this.' : '',
  }
}

/** The flag beside each staff member: it only means something while the agency setting is on. */
export function receivesFlagText(autoAssignOn: boolean | undefined): { label: string; muted: boolean; note: string } {
  return autoAssignOn === true
    ? { label: 'Can be given new candidates automatically', muted: false, note: '' }
    : { label: 'Can be given new candidates automatically', muted: true, note: 'Automatic assignment is off' }
}
