/**
 * The Campaigns tab of the Lead sheet: types of GET /api/leads/campaigns and the pure helpers the table uses (sorting, labels, the
 * little bars). No imports, so tests/lead-extras.test.mjs can load this file on its own.
 * Rates are percentages with one decimal (0 to 100), exactly as the server sends them.
 */
export interface CampaignPlatform { fb: number; ig: number; other: number }
export interface CampaignTop { name: string; count: number }

export interface CampaignStat {
  campaign: string | null
  form: string | null
  leads: number
  newToday: number
  last7Days: number
  assigned: number
  assignedPct: number
  contacted: number
  contactedPct: number
  interested: number
  resumeReceived: number
  converted: number
  conversionRate: number
  lastUploadAt: string | null
  platform: CampaignPlatform
  topPositions: CampaignTop[]
  topCountries: CampaignTop[]
  forms?: CampaignStat[]
}

export interface CampaignsResponse {
  scope: 'all' | 'mine'
  items: CampaignStat[]
  total: CampaignStat
  truncated: boolean
  generatedAt: string
}

/** A table row: a campaign, or the closing total row. */
export type CampaignRow = CampaignStat & { isTotal?: boolean }

export type CampaignSortKey =
  | 'campaign' | 'leads' | 'newToday' | 'last7Days' | 'assignedPct' | 'contactedPct' | 'interested' | 'resumeReceived' | 'converted' | 'conversionRate' | 'lastUploadAt'

export const CAMPAIGN_SORTS: readonly CampaignSortKey[] = [
  'campaign', 'leads', 'newToday', 'last7Days', 'assignedPct', 'contactedPct', 'interested', 'resumeReceived', 'converted', 'conversionRate', 'lastUploadAt',
]
export const isCampaignSort = (value: string): value is CampaignSortKey => (CAMPAIGN_SORTS as readonly string[]).includes(value)

export const NO_CAMPAIGN = 'No campaign'
export const campaignName = (row: Pick<CampaignStat, 'campaign'>) => (row.campaign ?? '').trim() || NO_CAMPAIGN

/** Sorted copy. Campaigns without a name always sit after the named ones; ties keep the "biggest first" order, then the name. */
export function sortCampaigns(rows: readonly CampaignStat[], key: CampaignSortKey, dir: 'asc' | 'desc'): CampaignStat[] {
  const sign = dir === 'asc' ? 1 : -1
  const value = (row: CampaignStat): number | string => {
    if (key === 'campaign') return campaignName(row).toLowerCase()
    if (key === 'lastUploadAt') return row.lastUploadAt ? Date.parse(row.lastUploadAt) || 0 : 0
    return row[key]
  }
  return [...rows].sort((a, b) => {
    const left = value(a), right = value(b)
    const by = typeof left === 'string' || typeof right === 'string' ? String(left).localeCompare(String(right)) : left - right
    if (by !== 0) return by * sign
    return b.leads - a.leads || campaignName(a).localeCompare(campaignName(b))
  })
}

/** The rows as shown: sorted campaigns, then the total row (when there is anything to total). */
export function tableRows(items: readonly CampaignStat[], total: CampaignStat | null | undefined, key: CampaignSortKey, dir: 'asc' | 'desc'): CampaignRow[] {
  const rows: CampaignRow[] = sortCampaigns(items, key, dir)
  return total && items.length > 0 ? [...rows, { ...total, isTotal: true }] : rows
}

export const rowId = (row: CampaignRow) => (row.isTotal ? '__total__' : `c:${row.campaign ?? ''}`)

/** "60%" or "12.5%": whole numbers stay whole. */
export const pctText = (value: number | null | undefined): string => {
  const n = Number.isFinite(value as number) ? (value as number) : 0
  return `${Number.isInteger(n) ? n : n.toFixed(1)}%`
}

/** Bar fill 0..100 for a percentage. A tiny non-zero rate still shows a sliver so it is not mistaken for zero. */
export const barFill = (pct: number | null | undefined): number => {
  const n = Number.isFinite(pct as number) ? (pct as number) : 0
  if (n <= 0) return 0
  return Math.max(3, Math.min(100, n))
}

/** Share of `part` in the platform split as a whole percentage (for the stacked bar); the parts add up to 100 when there are leads. */
export function platformShares(platform: CampaignPlatform): { fb: number; ig: number; other: number } {
  const total = platform.fb + platform.ig + platform.other
  if (total <= 0) return { fb: 0, ig: 0, other: 0 }
  const fb = Math.round((100 * platform.fb) / total)
  const ig = Math.round((100 * platform.ig) / total)
  return { fb, ig, other: Math.max(0, 100 - fb - ig) }
}

/** "Today", "Yesterday", "3 days ago" or the date: when the newest upload for a campaign arrived. */
export function uploadAgo(value: string | null | undefined, now: Date = new Date()): string {
  if (!value) return 'Never'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Never'
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  const days = Math.round((day(now) - day(date)) / 86_400_000)
  if (days <= 0) return 'Today'
  if (days === 1) return 'Yesterday'
  if (days < 14) return `${days} days ago`
  return date.toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' })
}

/** "Dubai 12, Qatar 3" for the top lists. */
export const topText = (items: readonly CampaignTop[], max = 3): string => items.slice(0, max).map((item) => `${item.name} ${item.count}`).join(', ')

/** What clicking a campaign row does to the Leads tab: filter by that campaign (nothing for the total row or an unnamed campaign). */
export function campaignFilterFor(row: Pick<CampaignRow, 'campaign' | 'isTotal'>): { campaign: string } | null {
  if (row.isTotal) return null
  const name = (row.campaign ?? '').trim()
  return name ? { campaign: name } : null
}

/** The empty-state wording by role. */
export function emptyCampaigns(scope: 'all' | 'mine' | undefined): { title: string; description: string } {
  return scope === 'mine'
    ? { title: 'No campaigns yet', description: 'When a manager assigns leads to you, the campaigns they came from are listed here with your own counts.' }
    : { title: 'No campaigns yet', description: 'Upload a lead sheet and every ad campaign in it gets a row here with its counts and conversion rate.' }
}
