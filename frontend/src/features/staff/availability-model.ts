import type { Role } from '@/shared/model/types'

export interface StaffAvailability {
  status: 'available' | 'approved_leave' | 'manual_leave' | 'paused' | 'inactive' | 'not_assigned'
  label: string
  eligible: boolean
  leaveType?: string
}

export interface AvailableStaff {
  id: string
  name: string
  email: string
  phone: string
  role: Role
  active: boolean
  availability: StaffAvailability
}

export interface StaffAvailabilityResult {
  today: string
  timezone: string
  staff: AvailableStaff[]
}

/** Pausing automatic rotation does not prevent a deliberate manual handover. */
export function canReceiveManualAssignment(staff: AvailableStaff) {
  return staff.active && staff.role === 'staff'
    && (staff.availability.status === 'available' || staff.availability.status === 'paused')
}

export function manualAssignmentLabel(staff: AvailableStaff) {
  return staff.availability.status === 'paused'
    ? `${staff.name} — Auto-assignment paused · available for manual assignment`
    : `${staff.name} — ${staff.availability.label}`
}
