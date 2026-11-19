import type { Role } from './types'

/**
 * What people read for each role, and the one rule for "admin level".
 *
 * The owner's rule: the role that used to read "Manager" is called "MD", and an MD has exactly the same access as an
 * Admin. The STORED values never change (API, database, session and capability tables still say `admin`, `manager`
 * and `staff`): only the words on screen differ. Always go through these helpers instead of printing `user.role`, and
 * use `isAdminLevel` for every "may an Admin do this" check, never `role === 'admin'`.
 * The backend twin is `backend/app/core/roles.py`.
 */
export const ROLE_LABELS: Record<Role, string> = { admin: 'Admin', manager: 'MD', staff: 'Recruiter' }

/** The longer form used under a person's name ("Administrator" instead of "Admin"). */
export const ROLE_TITLES: Record<Role, string> = { admin: 'Administrator', manager: 'MD', staff: 'Recruiter' }

/** The order a role picker offers them in. */
export const ROLE_ORDER: readonly Role[] = ['admin', 'manager', 'staff']

/** The roles that hold every privilege an Admin holds. */
export const ADMIN_LEVEL_ROLES: readonly Role[] = ['admin', 'manager']

/** A role value from the server or a form; `md` (any case) is the same role as the stored `manager`. */
export function normaliseRole(role: string | null | undefined): string {
  const key = String(role ?? '').trim().toLowerCase()
  return key === 'md' ? 'manager' : key
}

/** True for an Admin and for an MD (stored as `manager`): everything an Admin may do, an MD may do. */
export function isAdminLevel(role: string | null | undefined): boolean {
  const key = normaliseRole(role)
  return key === 'admin' || key === 'manager'
}

/** True only for a real Admin. For "the system keeps one admin" wording; never for a privilege. */
export function isRealAdmin(role: string | null | undefined): boolean {
  return normaliseRole(role) === 'admin'
}

/** The label inside a sentence or a bracket: lower case, except the acronym MD ("Asha (recruiter)", "Ravi (MD)"). */
export function roleWord(role: string | null | undefined): string {
  const label = roleLabel(role)
  return label === ROLE_LABELS.manager ? label : label.toLowerCase()
}

/** Display label for a role value from the server; an unknown value is shown as it came. */
export function roleLabel(role: string | null | undefined, long = false): string {
  if (!role) return ''
  const labels: Record<string, string> = long ? ROLE_TITLES : ROLE_LABELS
  return labels[normaliseRole(role)] ?? role
}
