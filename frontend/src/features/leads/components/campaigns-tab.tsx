import { useCallback, useEffect, useMemo, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { liveInterval } from '@/shared/state/live'
import { EmptyState } from '@/shared/ui/display'
import { Button, Select } from '@/shared/ui/primitives'
import { DbTable, useElementWidth, type DbColumn } from '@/features/candidates/components/db-table'
import { fitColumns } from '@/features/candidates/components/column-fit'
import { leadExtrasApi } from '../lead-extras-api'
import {
  CAMPAIGN_SORTS, barFill, campaignFilterFor, campaignName, emptyCampaigns, isCampaignSort, pctText, platformShares, rowId, tableRows, topText, uploadAgo,
  type CampaignRow, type CampaignSortKey, type CampaignStat, type CampaignsResponse,
} from '../campaigns-model'
import { LeadsSkeleton } from './leads-skeleton'
import '@/styles/lead-extras.css'

/** Below this box width the table gives way to cards. The table drops its least important columns first, so it still reads at 700px. */
const TABLE_MIN_WIDTH = 640
const SORT_LABEL: Record<CampaignSortKey, string> = {
  campaign: 'Campaign name', leads: 'Leads', newToday: 'New today', last7Days: 'New in 7 days', assignedPct: 'Assigned %', contactedPct: 'Contacted %',
  interested: 'Interested', resumeReceived: 'Resume received', converted: 'Converted', conversionRate: 'Conversion rate', lastUploadAt: 'Last upload',
}

const Num = ({ value, sub }: { value: number; sub?: string }) => (
  <span className={cn('lc-num tnum', value === 0 && 'is-zero')}><strong>{value.toLocaleString()}</strong>{sub ? <small>{sub}</small> : null}</span>
)
const Pct = ({ pct, count }: { pct: number; count: number }) => (
  <span className={cn('lc-num tnum', count === 0 && 'is-zero')}><strong>{pctText(pct)}</strong><small>{count.toLocaleString()}</small></span>
)

function ConversionCell({ row }: { row: CampaignStat }) {
  return (
    <span className="lc-conv" title={`${row.converted.toLocaleString()} of ${row.leads.toLocaleString()} leads became candidates (${pctText(row.conversionRate)})`}>
      <span className="lc-conv-top tnum"><strong>{pctText(row.conversionRate)}</strong><small>{row.converted.toLocaleString()}</small></span>
      <span className="lc-meter" role="img" aria-label={`Conversion ${pctText(row.conversionRate)}`}><i style={{ width: `${barFill(row.conversionRate)}%` }} /></span>
    </span>
  )
}

function PlatformCell({ row }: { row: CampaignStat }) {
  const share = platformShares(row.platform)
  return (
    <span className="lc-split" title={`Facebook ${row.platform.fb} · Instagram ${row.platform.ig} · other ${row.platform.other}`}>
      <span className="lc-split-bar" role="img" aria-label={`Facebook ${row.platform.fb}, Instagram ${row.platform.ig}, other ${row.platform.other}`}>
        <i className="is-fb" style={{ width: `${share.fb}%` }} /><i className="is-ig" style={{ width: `${share.ig}%` }} /><i className="is-other" style={{ width: `${share.other}%` }} />
      </span>
      <small className="tnum">FB {row.platform.fb} · IG {row.platform.ig}{row.platform.other ? ` · ${row.platform.other}` : ''}</small>
    </span>
  )
}

function NameCell({ row }: { row: CampaignRow }) {
  const forms = row.forms?.length ?? 0
  const sub = row.isTotal ? 'All campaigns' : !row.campaign ? 'Leads without a campaign name' : forms > 1 ? `${forms} forms` : (row.forms?.[0]?.form ?? '')
  return <span className="lc-name" title={[campaignName(row), sub].filter(Boolean).join(' · ')}><strong>{row.isTotal ? 'Total' : campaignName(row)}</strong>{sub ? <small>{sub}</small> : null}</span>
}

export function campaignColumns(): DbColumn<CampaignRow>[] {
  return [
    { key: 'campaign', header: 'Campaign', sortKey: 'campaign', width: 210, align: 'left', render: (r) => <NameCell row={r} /> },
    { key: 'leads', header: 'Leads', sortKey: 'leads', width: 70, render: (r) => <Num value={r.leads} /> },
    { key: 'new', header: 'New', sortKey: 'newToday', width: 92, optional: 3, render: (r) => <Num value={r.newToday} sub={`${r.last7Days.toLocaleString()} in 7 days`} /> },
    { key: 'assigned', header: 'Assigned', sortKey: 'assignedPct', width: 80, render: (r) => <Pct pct={r.assignedPct} count={r.assigned} /> },
    { key: 'contacted', header: 'Contacted', sortKey: 'contactedPct', width: 88, render: (r) => <Pct pct={r.contactedPct} count={r.contacted} /> },
    { key: 'interested', header: 'Interested', sortKey: 'interested', width: 84, render: (r) => <Num value={r.interested} /> },
    { key: 'resume', header: 'Resume in', sortKey: 'resumeReceived', width: 84, optional: 2, render: (r) => <Num value={r.resumeReceived} /> },
    { key: 'converted', header: 'Converted', sortKey: 'conversionRate', width: 128, render: (r) => <ConversionCell row={r} /> },
    { key: 'upload', header: 'Last upload', sortKey: 'lastUploadAt', width: 96, optional: 4, render: (r) => <span className="lc-num"><strong className="tnum" style={{ fontSize: 13 }}>{uploadAgo(r.lastUploadAt)}</strong></span> },
    { key: 'platform', header: 'Platform', width: 124, optional: 5, render: (r) => <PlatformCell row={r} /> },
    { key: 'positions', header: 'Top positions', width: 190, optional: 6, align: 'left', render: (r) => <span className="lc-text" title={topText(r.topPositions)}>{topText(r.topPositions) || '–'}</span> },
    { key: 'countries', header: 'Country interest', width: 130, optional: 7, align: 'left', render: (r) => <span className="lc-text" title={topText(r.topCountries)}>{topText(r.topCountries) || '–'}</span> },
  ]
}

function Expanded({ row }: { row: CampaignRow }) {
  const forms = row.forms ?? []
  return (
    <div className="lc-forms" onClick={(event) => event.stopPropagation()}>
      <dl className="lc-xfacts">
        <div><dt>Top positions</dt><dd>{topText(row.topPositions) || '–'}</dd></div>
        <div><dt>Country interest</dt><dd>{topText(row.topCountries) || '–'}</dd></div>
        <div><dt>Platform</dt><dd>Facebook {row.platform.fb} · Instagram {row.platform.ig} · other {row.platform.other}</dd></div>
        <div><dt>Last upload</dt><dd>{uploadAgo(row.lastUploadAt)}</dd></div>
      </dl>
      {forms.length > 0 && (
        <>
          <h4>Forms</h4>
          <table>
            <thead><tr><th>Form</th><th>Leads</th><th>Assigned</th><th>Contacted</th><th>Interested</th><th>Converted</th></tr></thead>
            <tbody>
              {forms.map((form) => (
                <tr key={form.form ?? ''}><td>{form.form}</td><td className="tnum">{form.leads.toLocaleString()}</td><td className="tnum">{pctText(form.assignedPct)}</td><td className="tnum">{pctText(form.contactedPct)}</td><td className="tnum">{form.interested.toLocaleString()}</td><td className="tnum">{form.converted.toLocaleString()} ({pctText(form.conversionRate)})</td></tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}

function CampaignCard({ row, onPick }: { row: CampaignRow; onPick: (row: CampaignRow) => void }) {
  const pick = campaignFilterFor(row)
  return (
    <li className={cn('lc-card', row.isTotal && 'is-total')}>
      <div className="lc-card-head">
        <strong>{row.isTotal ? 'Total' : campaignName(row)}</strong>
        <span className="lc-card-leads tnum">{row.leads.toLocaleString()}<small>leads</small></span>
      </div>
      <dl>
        <div><dt>New today</dt><dd className="tnum">{row.newToday.toLocaleString()}</dd></div>
        <div><dt>Assigned</dt><dd className="tnum">{pctText(row.assignedPct)}</dd></div>
        <div><dt>Contacted</dt><dd className="tnum">{pctText(row.contactedPct)}</dd></div>
        <div><dt>Interested</dt><dd className="tnum">{row.interested.toLocaleString()}</dd></div>
        <div><dt>Resume in</dt><dd className="tnum">{row.resumeReceived.toLocaleString()}</dd></div>
        <div><dt>Converted</dt><dd className="tnum">{row.converted.toLocaleString()}</dd></div>
      </dl>
      <span className="lc-meter" role="img" aria-label={`Conversion ${pctText(row.conversionRate)}`}><i style={{ width: `${barFill(row.conversionRate)}%` }} /></span>
      <div className="lc-card-foot">
        <small>{row.isTotal ? `${pctText(row.conversionRate)} converted` : `${pctText(row.conversionRate)} converted · last upload ${uploadAgo(row.lastUploadAt)}`}</small>
        {pick && <Button variant="secondary" size="sm" onClick={() => onPick(row)}>Show leads</Button>}
      </div>
    </li>
  )
}

interface Props {
  /** Switch to the Leads tab filtered by this campaign. */
  onPickCampaign: (campaign: string) => void
  /** Re-read when this changes (an upload finished). */
  refreshKey?: number
}

/**
 * Campaigns tab: one row per ad campaign with its counts, rates and a conversion bar, a total row, sortable columns, a row per form
 * under the arrow, cards on narrow widths. Clicking a campaign opens the Leads tab filtered by it. Staff see only their own leads.
 */
export function CampaignsTab({ onPickCampaign, refreshKey = 0 }: Props) {
  const [data, setData] = useState<CampaignsResponse | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [sort, setSort] = useState<{ key: CampaignSortKey; dir: 'asc' | 'desc' }>({ key: 'leads', dir: 'desc' })
  const [expanded, setExpanded] = useState<string[]>([])
  const [boxRef, boxWidth] = useElementWidth<HTMLDivElement>()

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const body = await leadExtrasApi.campaigns(signal)
      if (signal?.aborted) return
      setData(body); setError('')
    } catch (e) {
      if (!signal?.aborted) setError(e instanceof Error ? e.message : 'We could not load the campaigns.')
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    void load(controller.signal)
    const stop = liveInterval(() => load(controller.signal), 120_000, ['leads'], { debounceMs: 800 })
    return () => { controller.abort(); stop() }
  }, [load, refreshKey])

  const rows = useMemo(() => (data ? tableRows(data.items, data.total, sort.key, sort.dir) : []), [data, sort])
  const columns = useMemo(() => campaignColumns(), [])
  const fit = useMemo(() => fitColumns(columns, boxWidth, {}), [columns, boxWidth])
  const shown = useMemo(() => columns.filter((column) => fit.keys.includes(column.key)), [columns, fit])
  const cards = boxWidth > 0 && boxWidth < TABLE_MIN_WIDTH
  const pick = (row: CampaignRow) => { const target = campaignFilterFor(row); if (target) onPickCampaign(target.campaign) }
  const toggle = (id: string) => setExpanded((current) => (current.includes(id) ? current.filter((value) => value !== id) : [...current, id]))
  const onSort = (key: string) => { if (isCampaignSort(key)) setSort((current) => ({ key, dir: current.key === key && current.dir === 'desc' ? 'asc' : 'desc' })) }
  const scopeMine = data?.scope === 'mine'

  let body
  if (error && !data) body = <EmptyState title="We could not load the campaigns" description={error} action={<Button variant="primary" onClick={() => { setLoading(true); void load() }}>Try again</Button>} />
  else if (loading && !data) body = <LeadsSkeleton rows={6} columns={cards ? 3 : 8} label="Loading campaigns…" />
  else if (!data || data.items.length === 0) { const text = emptyCampaigns(data?.scope); body = <EmptyState title={text.title} description={text.description} /> }
  else if (cards) {
    body = (
      <ul className="lc-cards" aria-label="Campaigns">
        {rows.map((row) => <CampaignCard key={rowId(row)} row={row} onPick={pick} />)}
      </ul>
    )
  } else {
    body = (
      <DbTable<CampaignRow>
        variant="grid" fit expandedIds={expanded} onToggleExpand={toggle} renderExpanded={(row) => <Expanded row={row} />}
        label="Campaigns" rows={rows} columns={shown} rowKey={rowId} onRowClick={pick}
        rowClassName={(row) => cn(row.isTotal && 'lc-total')}
        sort={sort} onSort={onSort}
      />
    )
  }

  const campaignCount = data?.items.length ?? 0
  return (
    <div className="lc">
      <div className="lc-bar">
        <p className="lc-sum" role="status">
          {data ? <><strong>{campaignCount.toLocaleString()}</strong> {campaignCount === 1 ? 'campaign' : 'campaigns'} · <strong>{data.total.leads.toLocaleString()}</strong> {data.total.leads === 1 ? 'lead' : 'leads'}{scopeMine ? ' assigned to you' : ''}{loading ? ' · updating…' : ''}</> : loading ? 'Loading…' : ''}
        </p>
        <div className="lc-tools">
          {cards && (
            <Select aria-label="Sort campaigns by" value={`${sort.key}:${sort.dir}`} onChange={(event) => { const [key, dir] = event.target.value.split(':'); if (isCampaignSort(key)) setSort({ key, dir: dir as 'asc' | 'desc' }) }}>
              {CAMPAIGN_SORTS.flatMap((key) => [<option key={`${key}:desc`} value={`${key}:desc`}>{SORT_LABEL[key]} (high to low)</option>, <option key={`${key}:asc`} value={`${key}:asc`}>{SORT_LABEL[key]} (low to high)</option>])}
            </Select>
          )}
          <button type="button" className="db-refresh" title="Refresh the campaigns" aria-label="Refresh the campaigns" onClick={() => { setLoading(true); void load() }}><RefreshCw size={15} /></button>
        </div>
      </div>
      <p className="lc-tip">{scopeMine ? 'These are your own assigned leads. ' : ''}Click a campaign to see its leads. Rates are shares of the campaign's leads; Converted is the share that became candidates.</p>
      {data?.truncated && <p className="lc-tip" role="status">Only the 500 biggest campaigns are listed.</p>}
      {error && data && <p role="alert" className="db-alert">{error} Showing the last loaded figures.</p>}
      <div ref={boxRef} className={cn('db-box', loading && data && 'is-busy')} aria-busy={loading || undefined}>{body}</div>
    </div>
  )
}
