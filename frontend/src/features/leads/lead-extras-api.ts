/**
 * Requests of the Lead sheet extras (campaigns, WhatsApp templates and log, the candidate's lead origin).
 * Contract: docs/workflows/lead-sheet-contract.md ("Campaigns", "WhatsApp templates", "Candidate link").
 */
import { api } from '@/shared/api/client'
import type { CampaignsResponse } from './campaigns-model'
import { DEFAULT_WA_TEMPLATES, normalizeTemplates, type WaTemplate } from './wa-template-model'

const enc = encodeURIComponent

export interface WaTemplatesResponse { templates: WaTemplate[]; placeholders: string[]; companyName: string; bodyMax: number; titleMax: number }
export interface LeadOrigin { id: string; name?: string | null; campaignName?: string | null; formName?: string | null; status: string; converted: boolean }

export const leadExtrasApi = {
  campaigns: (signal?: AbortSignal) => api.get<CampaignsResponse>('/leads/campaigns', { signal }),
  waTemplates: (signal?: AbortSignal) => api.get<WaTemplatesResponse>('/leads/wa-templates', { signal }),
  saveWaTemplates: (templates: { key: string; title?: string; body?: string; reset?: boolean }[]) => api.put<WaTemplatesResponse>('/leads/wa-templates', { templates }),
  logWhatsApp: (leadId: string, body: { template: string | null; message: string }) => api.post<{ ok: boolean }>(`/leads/${enc(leadId)}/whatsapp`, body),
  originOf: (candidateId: string, signal?: AbortSignal) => api.get<{ leads: LeadOrigin[] }>(`/leads/for-candidate/${enc(candidateId)}`, { signal }),
}

/* The templates are read once per page load and shared by every WhatsApp popover; a failed read falls back to the defaults. */
let cached: Promise<{ templates: WaTemplate[]; companyName: string }> | null = null

export function loadWaTemplates(force = false): Promise<{ templates: WaTemplate[]; companyName: string }> {
  if (!cached || force) {
    cached = leadExtrasApi.waTemplates().then((body) => ({ templates: normalizeTemplates(body), companyName: body.companyName || '' }))
      .catch(() => { cached = null; return { templates: DEFAULT_WA_TEMPLATES, companyName: '' } })
  }
  return cached
}
