import type { Role } from './types'

/**
 * What people read for each role. The stored value of the staff role stays `staff` (API, database and permission
 * checks); only the words on screen say "Recruiter". Always go through these helpers instead of printing `user.role`.
 */
export const ROLE_LABELS: Record<Role, string> = { admin: 'Admin', manager: 'Manager', staff: 'Recruiter' }

/** The longer form used under a person's name ("Administrator" instead of "Admin"). */
export const ROLE_TITLES: Record<Role, string> = { admin: 'Administrator', manager: 'Manager', staff: 'Recruiter' }

/** Display label for a role value from the server; an unknown value is shown as it came. */
export function roleLabel(role: string | null | undefined, long = false): string {
  if (!role) return ''
  const labels: Record<string, string> = long ? ROLE_TITLES : ROLE_LABELS
  return labels[role] ?? role
}
