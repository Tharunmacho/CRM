import { api } from '@/shared/api/client'

export type PaymentMethod = 'cash' | 'bank' | 'upi' | 'cheque'
export interface CandidatePayment {
  id: string; amount: string; date: string; method: PaymentMethod; reference: string | null; note: string | null
  createdAt: string; createdBy: { id: string | null; name: string | null }; sourceStageId: string | null; voidedAt: string | null; voidReason: string | null
}
export interface CandidatePayments {
  invoiceId: string | null; invoiceNumber: string | null; invoiceStatus: string; currency: 'INR'; serviceFee: string | null; gst: string | null
  total: string | null; totalInvoiced: string; received: string; balance: string | null; credit: string; canRecord: boolean; canVoid: boolean
  payments: CandidatePayment[]
  availableAdvances: { sourceStageId: string; amount: string; date: string; method: PaymentMethod | null; reference: string | null }[]
}
export interface PaymentDraft { applicationId?: string; amount: string; date: string; method: PaymentMethod; reference?: string; note?: string; idempotencyKey: string; sourceStageId?: string }
export const candidatePaymentsPath = (candidateId: string, applicationId?: string | null) => `/candidates/${encodeURIComponent(candidateId)}/invoice/payments${applicationId ? `?applicationId=${encodeURIComponent(applicationId)}` : ''}`
export const recordCandidatePayment = (candidateId: string, draft: PaymentDraft) => api.post<CandidatePayments>(candidatePaymentsPath(candidateId, draft.applicationId), draft)
export const voidCandidatePayment = (candidateId: string, paymentId: string, reason: string) => api.post<CandidatePayments>(`/candidates/${encodeURIComponent(candidateId)}/invoice/payments/${encodeURIComponent(paymentId)}/void`, { reason })
