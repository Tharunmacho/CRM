export type ChatConversation = {
  id: string; phoneNumberId: string; peerNumber: string; displayName: string | null;
  lastInboundAt: string | null; lastMessageAt: string | null; lastPreview: string | null;
  candidate: { id: string; ref: string; name: string; ownerId: string | null } | null;
  canSend: boolean; readOnlyReason: string | null; serviceWindowOpen: boolean;
  serviceWindowExpiresAt: string | null; channelEnabled: boolean;
}
export type ChatMessage = {
  id: string; direction: 'inbound' | 'outbound'; type: string; body: string | null;
  at: string; state: string; providerMessageId: string | null; candidateId: string | null;
  fileName: string | null; contentType: string | null; hasFile: boolean; fileUrl: string | null;
  template?: { name: string; language: string }; errorCode?: string; lastError?: string;
}
export type ChatTemplate = {
  id: string; name: string; language: string; category: string; status: string; body: string;
  parameterCount: number; parameters: { index: number; label: string }[];
  supported: boolean; unsupportedReason: string | null; syncedAt: string;
}
export type ConversationPage = { total: number; conversations: ChatConversation[] }
export type MessagePage = { conversation: ChatConversation; messages: ChatMessage[]; nextBefore: string | null }

export function mergeChatMessages(older: ChatMessage[], newer: ChatMessage[]) {
  const messages = new Map<string, ChatMessage>()
  for (const message of [...older, ...newer]) messages.set(`${message.direction}:${message.id}`, message)
  return [...messages.values()].sort((a, b) => a.at.localeCompare(b.at) || a.id.localeCompare(b.id))
}

export function templatePreview(template: ChatTemplate | undefined, parameters: string[]) {
  return template?.body.replace(/\{\{\s*(\d+)\s*\}\}/g, (match, index) => parameters[Number(index) - 1] || match) ?? ''
}

/** Only a supervisor may link a candidate while opening a conversation (the server answers 403 otherwise). */
export const canLinkCandidate = (role: string): boolean => role === 'admin' || role === 'manager'

export function openConversationBody(input: { phoneNumberId: string; peerNumber: string; candidateId: string; role: string }) {
  const { phoneNumberId, peerNumber, candidateId, role } = input
  return { phoneNumberId, peerNumber, ...(candidateId && canLinkCandidate(role) ? { candidateId } : {}) }
}

/** The deep link the candidate profile uses to open a candidate's linked conversation. */
export const conversationHref = (conversationId: string): string => `/whatsapp?conversation=${encodeURIComponent(conversationId)}`

/** The conversation a `?conversation=` deep link asks for; anything that is not a plain identifier is ignored. */
export function conversationFromSearch(search: string): string {
  const value = new URLSearchParams(search).get('conversation') ?? ''
  return /^[A-Za-z0-9_-]{1,64}$/.test(value) ? value : ''
}
