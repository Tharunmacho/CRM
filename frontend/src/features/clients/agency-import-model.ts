import type { ReferenceAgency } from '../../shared/model/types'

export const AGENCY_IMPORT_FIELDS = [
  ['name', 'Contact person'], ['agencyName', 'Agency name'], ['phone', 'Contact number'],
  ['place', 'Place / City'], ['address', 'Address'], ['state', 'State'], ['email', 'Email'], ['notes', 'Notes'],
] as const
export type AgencyImportField = typeof AGENCY_IMPORT_FIELDS[number][0]
export type AgencyImportMapping = Partial<Record<AgencyImportField, number>>
export interface AgencyImportOptions { mapping: AgencyImportMapping; headerRow: number; sheetName: string }
export type AgencyImportStatus = 'create' | 'add_contact' | 'duplicate' | 'conflict' | 'invalid' | 'section'
export interface AgencyImportRow {
  rowNumber: number
  status: AgencyImportStatus
  message: string
  values: Record<AgencyImportField, string | null>
  agencyId?: string
}
export interface AgencyImportPreview extends AgencyImportOptions {
  fileName: string
  sheets: string[]
  columns: { index: number; label: string; sample: string[] }[]
  previewHash: string
  rows: AgencyImportRow[]
  counts: Record<AgencyImportStatus, number>
  canConfirm: boolean
}
export interface AgencyImportResult {
  created: number
  contactsAdded: number
  skipped: number
  conflicts: number
  invalid: number
  items: ReferenceAgency[]
}

export const AGENCY_IMPORT_STATUS: Record<AgencyImportStatus, string> = {
  create: 'New agency', add_contact: 'Add contact', duplicate: 'Duplicate · skipped',
  conflict: 'Conflict · skipped', invalid: 'Invalid · skipped', section: 'Section heading',
}

export function agencyImportFileError(file: Pick<File, 'name' | 'size'>): string {
  if (!/\.(xlsx|csv)$/i.test(file.name)) return 'Choose an Excel (.xlsx) or CSV file.'
  if (file.size === 0) return 'This file is empty. Choose a file with agency details.'
  if (file.size > 5 * 1024 * 1024) return 'Choose a file of 5 MB or less.'
  return ''
}

/** Only the exact reviewed sheet/header/mapping can be confirmed. Key order has no meaning. */
export function sameAgencyImportOptions(left: AgencyImportOptions, right: AgencyImportOptions): boolean {
  return left.headerRow === right.headerRow && left.sheetName === right.sheetName
    && AGENCY_IMPORT_FIELDS.every(([key]) => left.mapping[key] === right.mapping[key])
}

export function agencyImportReadyCount(preview: AgencyImportPreview | null): number {
  return preview ? preview.counts.create + preview.counts.add_contact : 0
}
