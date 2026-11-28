import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUpRight, FileSpreadsheet, Loader2, Route } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { agencyDay } from '@/shared/lib/task-time'
import { downloadClientTracker, useClientTracker } from './client-tracker-api'
import { EMPTY_TRACKER_FILTERS } from './client-tracker-model'
import { summarizeTracker, trackerLink } from './tracker-summary'
import '@/styles/dashboard-tracker.css'

const count = (value: number) => value.toLocaleString('en-IN')

/**
 * The Dashboard's one Client tracker card: how many placements are in progress and how many travelled, and how many wait at each
 * stage a recruiter chases. It reads the same request as the Client tracker tab (so the answer is shared) but only once the card
 * scrolls into view, so it never slows the rest of the Dashboard.
 */
export function DashboardTrackerCard() {
  const holder = useRef<HTMLElement | null>(null)
  const [near, setNear] = useState(false)
  useEffect(() => {
    const node = holder.current
    if (!node || near) return undefined
    if (typeof IntersectionObserver === 'undefined') { const timer = window.setTimeout(() => setNear(true), 1500); return () => window.clearTimeout(timer) }
    const watcher = new IntersectionObserver(entries => { if (entries.some(entry => entry.isIntersecting)) setNear(true) }, { rootMargin: '240px' })
    watcher.observe(node)
    return () => watcher.disconnect()
  }, [near])
  return <section ref={holder} className="dt-card" aria-label="Client tracker">
    <header className="dt-head">
      <span className="dt-icon"><Route size={20} strokeWidth={1.9} /></span>
      <div><h2>Client tracker</h2></div>
    </header>
    {near ? <TrackerBody /> : <div className="dt-note" aria-busy="true">Loading the tracker</div>}
  </section>
}

function TrackerBody() {
  const store = useStore()
  const { data, error, refresh } = useClientTracker(EMPTY_TRACKER_FILTERS, store.me.id)
  const summary = useMemo(() => data ? summarizeTracker(data.rows) : null, [data])
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState('')
  async function exportFile() {
    setExporting(true); setExportError('')
    try { await downloadClientTracker(EMPTY_TRACKER_FILTERS, 'xlsx', agencyDay(new Date(), store.agencyTimezone)) }
    catch (failure) { setExportError(failure instanceof Error ? failure.message : 'We could not create the file. Please try again.') }
    finally { setExporting(false) }
  }
  if (!summary) {
    if (error) return <div role="alert" className="dt-note dt-fail">Could not load the tracker.<button type="button" onClick={refresh}>Try again</button></div>
    return <div className="dt-note" aria-busy="true" role="status">Loading the tracker</div>
  }
  if (!summary.total) return <>
    <p className="dt-note" role="status">No placements are on the tracker yet.</p>
    <div className="dt-actions"><Link className="dt-btn" to={trackerLink()}>Open tracker<ArrowUpRight size={15} aria-hidden="true" /></Link></div>
  </>
  return <>
    <div className="dt-totals">
      <div><b>{count(summary.inProgress)}</b><span>in progress</span></div>
      <div><b>{count(summary.travelled)}</b><span>travelled</span></div>
    </div>
    <div>
      <p className="dt-sub">Waiting at</p>
      <ul className="dt-chips">{summary.waiting.map(stage => <li key={stage.no}>
        <Link className={`dt-chip${stage.count ? '' : ' is-zero'}`} to={trackerLink(stage.no)} aria-label={`${stage.label}: ${count(stage.count)} waiting`}>
          <span>{stage.label}</span><b>{count(stage.count)}</b>
        </Link>
      </li>)}</ul>
    </div>
    <div className="dt-actions">
      <Link className="dt-btn" to={trackerLink()}>Open tracker<ArrowUpRight size={15} aria-hidden="true" /></Link>
      <button type="button" className="dt-btn" onClick={() => void exportFile()} disabled={exporting}>
        {exporting ? <Loader2 size={15} className="dt-spin" aria-hidden="true" /> : <FileSpreadsheet size={15} aria-hidden="true" />}{exporting ? 'Creating the file' : 'Export Excel'}
      </button>
      {exportError && <span role="alert" className="dt-fail dt-inline">{exportError}</span>}
    </div>
  </>
}
