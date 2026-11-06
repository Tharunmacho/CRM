/**
 * The unread-mail count the desk response carries (`inboxUnread: { total, accounts }`, same shape as `unread` in
 * /api/inbox/pulse). `null` when the server is older and omits it (or sends something unusable), in which case the
 * first pulse after load supplies the number.
 */
export function deskUnread(payload: { inboxUnread?: { total?: unknown } | null } | null | undefined): number | null {
  const total = payload?.inboxUnread?.total
  return typeof total === 'number' && Number.isFinite(total) && total >= 0 ? total : null
}
