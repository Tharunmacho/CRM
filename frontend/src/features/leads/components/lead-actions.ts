import { FileSearch } from 'lucide-react'
import type { RowMenuItem } from '@/features/candidates/components/db-parts'
import type { User } from '@/shared/model/types'
import { leadRefusal, type Lead, type LeadActor } from '../lead-model'
import { GATE_REASON, gateBlocks, type GateState } from '../lead-gate'

export interface LeadColumnContext {
  me: LeadActor
  gate: GateState
  userById: (id: string | null | undefined) => User | undefined
  onOpen: (lead: Lead) => void
  /** The "Assigned to" cell: opens the remarks and what-was-done panel (assigned leads only). */
  onViewRemarks?: (lead: Lead) => void
  onLogCall: (lead: Lead) => void
  onConvert: (lead: Lead) => void
  onUploadResume: (lead: Lead, file: File) => void
}

/** The reason a work action on this lead is unavailable: the daily-work gate first, then ownership. */
export function workBlock(context: Pick<LeadColumnContext, 'me' | 'gate' | 'userById'>, lead: Lead): string | null {
  if (gateBlocks(context.gate)) return GATE_REASON
  return leadRefusal(context.me, lead, context.userById(lead.ownerId)?.name)
}

/**
 * The row "..." menu is deliberately tiny: Open lead only.
 * Every other action (edit, remarks, convert, assign, WhatsApp, status, not interested,
 * delete, move back) lives inside the opened lead.
 */
export function leadMenuItems(context: LeadColumnContext, lead: Lead): RowMenuItem[] {
  return [
    { label: 'Open lead', icon: FileSearch, onSelect: () => context.onOpen(lead) },
  ]
}
