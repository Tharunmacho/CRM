import { api } from '@/shared/api/client'

export interface CandidateInvoiceState {
  candidateId: string
  applicationId: string | null
  canEdit: boolean
  status: 'pending_assignment' | 'pending_ticket' | 'pending_fee' | 'pending_configuration' | 'pending_generation' | 'issued'
  serviceFee: string | null
  currency: 'INR'
  ticketUploaded: boolean
  lockVersion: number
  missingConfiguration: string[]
  error?: string | null
  taxRates?: { cgst: string; sgst: string }
  issuer?: { companyName: string; gstin: string }
  invoice: { id: string; number: string | null; issuedAt: string | null; subtotal: string | null; cgstRate: string; sgstRate: string; cgst: string | null; sgst: string | null; total: string | null; downloadUrl: string | null } | null
}

export interface InvoiceSettingsFields {
  companyName: string
  address: string
  gstin: string
  invoicePrefix: string
  nextNumber: number | null
  cgstRate: string
  sgstRate: string
  sacCode: string
  bankName: string
  bankAccount: string
  bankIfsc: string
}
export interface InvoiceSettingsState { canEdit: boolean; configured: boolean; missing: string[]; settings: InvoiceSettingsFields; lockVersion: number }

function invoicePath(candidateId: string, applicationId?: string | null, suffix = '') {
  const query = applicationId ? `?applicationId=${encodeURIComponent(applicationId)}` : ''
  return `/candidates/${encodeURIComponent(candidateId)}/invoice${suffix}${query}`
}

export const loadCandidateInvoice = (candidateId: string, applicationId?: string | null) => api.get<CandidateInvoiceState>(invoicePath(candidateId, applicationId))
export const saveCandidateFee = (candidateId: string, applicationId: string | null, serviceFee: string, lockVersion: number) =>
  api.put<CandidateInvoiceState>(invoicePath(candidateId, applicationId), { serviceFee, applicationId, lockVersion })
export const reconcileCandidateInvoice = (candidateId: string, applicationId?: string | null) =>
  api.post<CandidateInvoiceState>(invoicePath(candidateId, applicationId, '/reconcile'), { applicationId: applicationId ?? undefined })
export const loadInvoiceSettings = () => api.get<InvoiceSettingsState>('/invoice-settings')
export const saveInvoiceSettings = (settings: InvoiceSettingsFields, lockVersion: number) => api.put<InvoiceSettingsState>('/invoice-settings', { ...settings, lockVersion })
