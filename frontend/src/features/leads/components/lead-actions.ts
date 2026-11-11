import { FileSearch, PhoneCall, UserPlus, UserCheck, MessageCircle, ListChecks, UserRoundX, Trash2 } from 'lucide-react'
import { whatsappHref } from '@/features/candidates/data-bank-view'
import type { RowMenuItem } from '@/features/candidates/components/db-parts'
import type { User } from '@/shared/model/types'
import { leadRefusal, type Lead, type LeadActor } from '../lead-model'
import { GATE_REASON, gateBlocks, type GateState } from '../lead-gate'
import { openWhatsApp } from './wa-template'

export interface LeadColumnContext {
  me: LeadActor
  gate: GateState
  userById: (id: string | null | undefined) => User | undefined
  canAssign: boolean
  onOpen: (lead: Lead) => void
  onLogCall: (lead: Lead) => void
  onConvert: (lead: Lead) => void
  onUploadResume: (lead: Lead, file: File) => void
  onAssign: (lead: Lead) => void
  onNotInterested: (lead: Lead) => void
  /** Row menu "Change status" (opens the status dialog; an undo toast follows). */
  onChangeStatus?: (lead: Lead) => void
  /** Admin only: delete one lead (row menu and drawer). */
  canDelete?: boolean
  onDelete?: (lead: Lead) => void
}

/** The reason a work action on this lead is unavailable: the daily-work gate first, then ownership. */
export function workBlock(context: Pick<LeadColumnContext, 'me' | 'gate' | 'userById'>, lead: Lead): string | null {
  if (gateBlocks(context.gate)) return GATE_REASON
  return leadRefusal(context.me, lead, context.userById(lead.ownerId)?.name)
}

export function leadMenuItems(context: LeadColumnContext, lead: Lead): RowMenuItem[] {
  const block = workBlock(context, lead)
  const convert = lead.candidateId ? 'This lead is already a candidate.' : block ?? undefined
  const wa = whatsappHref(lead.phone)
  const items: RowMenuItem[] = [
    { label: 'Open lead', icon: FileSearch, onSelect: () => context.onOpen(lead) },
    { label: 'Log a call', icon: PhoneCall, onSelect: () => context.onLogCall(lead), disabledReason: block ?? undefined },
    { label: 'Convert to candidate', icon: UserPlus, onSelect: () => context.onConvert(lead), disabledReason: convert },
  ]
  if (context.canAssign) items.push({ label: 'Assign', icon: UserCheck, onSelect: () => context.onAssign(lead) })
  items.push({ label: 'WhatsApp message…', icon: MessageCircle, onSelect: () => { if (wa) openWhatsApp(lead) }, disabledReason: wa ? undefined : 'No valid phone number' })
  const converted = lead.candidateId || lead.status === 'converted' ? 'This lead is already a candidate.' : undefined
  if (context.onChangeStatus) items.push({ label: 'Change status', icon: ListChecks, onSelect: () => context.onChangeStatus?.(lead), disabledReason: block ?? converted })
  items.push({ label: 'Not interested', icon: UserRoundX, onSelect: () => context.onNotInterested(lead), disabledReason: block ?? converted })
  if (context.canDelete && context.onDelete) items.push({ label: 'Delete lead', icon: Trash2, onSelect: () => context.onDelete?.(lead), disabledReason: lead.candidateId ? 'This lead was converted to a candidate, so it cannot be deleted.' : undefined })
  return items
}
