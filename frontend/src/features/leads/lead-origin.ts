/** Text of the "From lead sheet" badge on a candidate profile (pure, no imports). */
export function originLabel(lead: { campaignName?: string | null; formName?: string | null; name?: string | null }): string {
  const where = (lead.campaignName ?? '').trim() || (lead.formName ?? '').trim() || (lead.name ?? '').trim()
  return where ? `From lead sheet: ${where}` : 'From lead sheet'
}
