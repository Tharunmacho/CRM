import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertCircle, ChevronLeft, ChevronRight, Download, Loader2 } from 'lucide-react'
import { Button } from '@/shared/ui/primitives'
import { Sheet } from '@/shared/ui/overlay'
import { dailyReportApi, saveCsv } from './daily-report-api'
import { buildCsv, clockTime, csvFileName, dayInZone, drillHref, drillWhenHeading, isSnapshotMetric, numberText, pageCount, pageRange, rangeLabel, shortDay } from './daily-report-view'
import type { DailyQuery, DrillPage, DrillRow } from './daily-report-types'

const PAGE_SIZE = 25

function Who({ row }: { row: DrillRow }) {
  const href = drillHref(row)
  return <>
    {href ? <Link to={href} className="dr-link">{row.name || 'Unnamed'}</Link> : <span>{row.name || 'Unnamed'}</span>}
    {row.careId && <small className="dr-ci">{row.careId}</small>}
    {row.kind === 'lead' && <small className="dr-ci is-lead">Lead</small>}
  </>
}

/** The shortest time between two quiet re-reads of the open list. */
export const DRILL_REREAD_MS = 10_000

/** The list behind a number: who, when, and what, one page at a time, with the same list as a CSV. */
/** `version` changes every time the report was read again: the open page is then read again quietly, without a flash. */
export function DrilldownDrawer({ metric, title, query, today, timeZone, version, onClose }: { metric: string | null; title: string; query: DailyQuery; today: string; timeZone: string; version: number; onClose: () => void }) {
  const [page, setPage] = useState(0)
  const [data, setData] = useState<DrillPage | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [downloading, setDownloading] = useState(false)
  const [retry, setRetry] = useState(0)
  const { from, to, staff, client, all } = query

  useEffect(() => { setPage(0); setData(null) }, [metric, from, to, staff, client, all])
  const seen = useRef('')
  // The report is re-read every few seconds while it is live; the open list is re-read for that at most once per DRILL_REREAD_MS (the first change at once,
  // later ones wait out the gap), so an open drawer does not double the load of the page behind it. Opening, paging or changing the choice always reads now.
  const lastRead = useRef(0)
  const [settled, setSettled] = useState(version)
  useEffect(() => {
    if (version === settled) return undefined
    const wait = Math.max(0, DRILL_REREAD_MS - (Date.now() - lastRead.current))
    const timer = window.setTimeout(() => setSettled(version), wait)
    return () => window.clearTimeout(timer)
  }, [version, settled])
  useEffect(() => {
    if (!metric) { seen.current = ''; return undefined }
    lastRead.current = Date.now()
    const controller = new AbortController()
    const quiet = seen.current === `${metric}|${from}|${to}|${staff}|${client}|${page}`
    seen.current = `${metric}|${from}|${to}|${staff}|${client}|${page}`
    if (!quiet) setLoading(true)
    setError('')
    dailyReportApi.drilldown(metric, { from, to, staff, client, all }, page, PAGE_SIZE, controller.signal)
      .then(result => { if (!controller.signal.aborted) setData(result) })
      .catch(failure => { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'The list could not be loaded.') })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [metric, from, to, staff, client, all, page, retry, settled])

  const label = data?.label || title
  const size = data?.pageSize || PAGE_SIZE
  const pages = data ? pageCount(data.total, size) : 1
  const rows = data?.rows ?? []

  async function downloadAll() {
    if (!metric || !data) return
    setDownloading(true)
    try {
      let collected: DrillRow[] = rows
      if (data.total > rows.length) {
        collected = []
        for (let index = 0, guard = 0; guard < 60; index += 1, guard += 1) {
          const part = await dailyReportApi.drilldown(metric, { from, to, staff, client, all }, index, 100)
          collected.push(...part.rows)
          if (!part.rows.length || collected.length >= part.total) break
        }
      }
      saveCsv(buildCsv(collected, timeZone, metric), csvFileName(label, from, to, isSnapshotMetric(metric) ? today : undefined))
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'The file could not be created.')
    } finally { setDownloading(false) }
  }

  const footer = data && data.total > 0
    ? <div className="dr-drill-foot">
      <span className="dr-drill-range" role="status">{pageRange(data.total, data.page ?? page, size)}</span>
      <div className="dr-drill-pager" role="group" aria-label="Pages">
        <Button size="sm" icon={<ChevronLeft size={14} />} disabled={loading || page <= 0} onClick={() => setPage(page - 1)} aria-label="Previous page" />
        <span>Page {page + 1} of {pages}</span>
        <Button size="sm" icon={<ChevronRight size={14} />} disabled={loading || page + 1 >= pages} onClick={() => setPage(page + 1)} aria-label="Next page" />
      </div>
      <Button size="sm" icon={downloading ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />} disabled={downloading} onClick={() => void downloadAll()}>
        {downloading ? 'Preparing…' : data.total > rows.length ? `Download all ${data.total.toLocaleString('en-IN')} (CSV)` : 'Download (CSV)'}
      </Button>
    </div>
    : undefined

  return <Sheet open={!!metric} onClose={onClose} title={label || 'Details'} description={`${metric && isSnapshotMetric(metric) ? 'Right now (not limited to the chosen period)' : query.all ? "All time" : rangeLabel(from, to, today)}${data ? ` · ${numberText(data.total)} ${data.total === 1 ? 'entry' : 'entries'}` : ''}`} width="xl" footer={footer}>
    <div className="dr-drill" aria-busy={loading}>
      {error && <div className="dr-alert" role="alert"><span><AlertCircle size={15} aria-hidden="true" />{error}</span><button type="button" onClick={() => setRetry(n => n + 1)}>Try again</button></div>}
      {!data && loading && <div className="dr-drill-skel" role="status" aria-label="Loading the list">{[0, 1, 2, 3, 4, 5].map(i => <i key={i} />)}</div>}
      {data && data.total === 0 && !loading && <div className="dr-empty-note is-block" role="status"><strong>Nothing in this list</strong><p>No entries were recorded for this figure in the chosen period and filters.</p></div>}
      {data && rows.length > 0 && <div className={loading ? 'dr-drill-scroll is-stale' : 'dr-drill-scroll'}>
        <table className="dr-drill-table">
          <caption className="dr-sr">{label}</caption>
          <thead><tr><th scope="col">{metric ? drillWhenHeading(metric) : 'When'}</th><th scope="col">Name</th><th scope="col">Recruiter</th><th scope="col">Client</th><th scope="col">Job</th><th scope="col">Detail</th></tr></thead>
          <tbody>{rows.map((row, index) => <tr key={`${row.at}-${row.candidateId ?? row.leadId ?? index}-${index}`}>
            <td className="dr-when"><b>{clockTime(row.at, timeZone)}</b><small>{shortDay(dayInZone(row.at, timeZone))}</small></td>
            <td className="dr-who"><Who row={row} /></td>
            <td>{row.staffName}</td>
            <td>{row.client || '–'}</td>
            <td>{row.job || '–'}</td>
            <td className="dr-detail">{row.detail || '–'}</td>
          </tr>)}</tbody>
        </table>
      </div>}
    </div>
  </Sheet>
}
