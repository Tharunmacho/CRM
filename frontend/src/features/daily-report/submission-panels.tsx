import { useEffect, useState } from 'react'
import { AlertCircle, ChevronLeft, ChevronRight } from 'lucide-react'
import { Button } from '@/shared/ui/primitives'
import { Sheet } from '@/shared/ui/overlay'
import { dailyReportApi } from './daily-report-api'
import { ApiError } from '@/shared/api/client'
import {
  changedSinceText, clockTime, DAILY_ORDER, dailyLabel, dayLabel, excerpt, figuresLine, isOutdatedServer, numberText, pageCount, pageRange, rangeLabel, shortDay,
} from './daily-report-view'
import type { DailyQuery, HistoryPage, StaffRow } from './daily-report-types'

/**
 * Who submitted, when, their note and the figures they submitted: opened by a "Submitted" pill in the table. A quiet line says
 * so when the numbers moved after the report was sent.
 */
export function SubmissionPanel({ row, day, today, timeZone, onClose }: { row: StaffRow | null; day: string; today: string; timeZone: string; onClose: () => void }) {
  const changes = row ? changedSinceText(row) : ''
  const time = row ? clockTime(row.submittedAt, timeZone) : ''
  return <Sheet open={!!row} onClose={onClose} title={row ? `${row.name}'s report` : 'Report'} description={row ? `${dayLabel(day, today)}${time ? ` · submitted at ${time}` : ''}` : undefined} width="md">
    {row && <div className="dr-drill">
      <section className="dr-block">
        <h3>Note</h3>
        {row.note ? <p className="dr-note-text">{row.note}</p> : <p className="dr-muted">No note was added.</p>}
      </section>
      <section className="dr-block">
        <h3>Figures submitted</h3>
        {row.submittedFigures
          ? <table className="dr-drill-table dr-mini">
            <thead><tr><th scope="col">Figure</th><th scope="col" className="dr-num">Submitted</th><th scope="col" className="dr-num">Now</th></tr></thead>
            <tbody>{DAILY_ORDER.map(key => {
              const sent = row.submittedFigures?.[key] ?? 0
              const now = key === 'interviewsOnline' ? row.interviewsOnline ?? 0 : row.metrics[key] ?? 0
              return <tr key={key}><th scope="row">{dailyLabel(key, false)}</th><td className="dr-num">{numberText(sent)}</td><td className={now !== sent ? 'dr-num is-changed' : 'dr-num'}>{numberText(now)}</td></tr>
            })}</tbody>
          </table>
          : <p className="dr-muted">The figures of this report were not kept.</p>}
        {changes && <p className="dr-muted" role="status">{changes}.</p>}
      </section>
    </div>}
  </Sheet>
}

/** Every submitted report in the period, newest first, a page at a time: date, who, when, a note excerpt and the key figures. */
export function HistoryDrawer({ open, query, today, timeZone, version, onClose }: { open: boolean; query: DailyQuery; today: string; timeZone: string; version: number; onClose: () => void }) {
  const [page, setPage] = useState(0)
  const [data, setData] = useState<HistoryPage | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const { from, to, staff, client, all } = query
  useEffect(() => { setPage(0); setData(null) }, [open, from, to, staff, client, all])
  useEffect(() => {
    if (!open) return undefined
    const controller = new AbortController()
    setLoading(true); setError('')
    dailyReportApi.history({ from, to, staff, client, all }, page, 25, controller.signal)
      .then(result => { if (!controller.signal.aborted) setData(result) })
      .catch(failure => {
        if (controller.signal.aborted) return
        const status = failure instanceof ApiError ? failure.status : undefined
        setError(isOutdatedServer(status) ? 'This list needs the latest server version.' : failure instanceof Error ? failure.message : 'The list could not be loaded.')
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [open, from, to, staff, client, all, page, version])
  const size = data?.pageSize || 25
  const pages = data ? pageCount(data.total, size) : 1
  const footer = data && data.total > 0
    ? <div className="dr-drill-foot">
      <span className="dr-drill-range" role="status">{pageRange(data.total, data.page ?? page, size)}</span>
      <div className="dr-drill-pager" role="group" aria-label="Pages">
        <Button size="sm" icon={<ChevronLeft size={14} />} disabled={loading || page <= 0} onClick={() => setPage(page - 1)} aria-label="Previous page" />
        <span>Page {page + 1} of {pages}</span>
        <Button size="sm" icon={<ChevronRight size={14} />} disabled={loading || page + 1 >= pages} onClick={() => setPage(page + 1)} aria-label="Next page" />
      </div>
    </div>
    : undefined
  return <Sheet open={open} onClose={onClose} title="Submitted reports" description={query.all ? "All time" : rangeLabel(from, to, today)} width="xl" footer={footer}>
    <div className="dr-drill" aria-busy={loading}>
      {error && <div className="dr-alert" role="alert"><span><AlertCircle size={15} aria-hidden="true" />{error}</span></div>}
      {!data && loading && <div className="dr-drill-skel" role="status" aria-label="Loading the list">{[0, 1, 2, 3, 4].map(i => <i key={i} />)}</div>}
      {data && data.total === 0 && <div className="dr-empty-note" role="status"><strong>No reports submitted</strong><p>Nobody has submitted a report in this period.</p></div>}
      {data && data.rows.length > 0 && <div className="dr-drill-scroll">
        <table className="dr-drill-table">
          <caption className="dr-sr">Submitted reports</caption>
          <thead><tr><th scope="col">Day</th><th scope="col">Name</th><th scope="col">Submitted</th><th scope="col">Note</th><th scope="col">Key figures</th></tr></thead>
          <tbody>{data.rows.map(row => <tr key={`${row.date}-${row.staffId}`}>
            <td className="dr-when"><b>{shortDay(row.date)}</b></td>
            <td className="dr-who">{row.staffName}</td>
            <td className="dr-when"><b>{clockTime(row.submittedAt, timeZone)}</b></td>
            <td className="dr-detail">{excerpt(row.note) || '–'}</td>
            <td className="dr-detail">{figuresLine(row.figures)}</td>
          </tr>)}</tbody>
        </table>
      </div>}
    </div>
  </Sheet>
}
