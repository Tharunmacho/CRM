import { useCallback, useEffect, useRef, useState } from 'react'
import { Check, Loader2 } from 'lucide-react'
import { Button, Textarea } from '@/shared/ui/primitives'
import { dailyReportApi } from './daily-report-api'
import { canSubmitDay, clockTime, shiftDay } from './daily-report-view'
import type { MyDay } from './daily-report-types'

const NOTE_LIMIT = 500

/**
 * "My day": one small card under the table. The figures are the table's own row; here the recruiter adds a note and
 * confirms the day before closing their work (today or yesterday).
 */
export function MyDayCard({ today, timeZone, refreshKey, onSubmitted, focus = false }: { today: string; timeZone: string; refreshKey: number; onSubmitted: () => void; focus?: boolean }) {
  const [which, setWhich] = useState<'today' | 'yesterday'>('today')
  const date = which === 'today' ? today : shiftDay(today, -1)
  const [day, setDay] = useState<MyDay | null>(null)
  const [note, setNote] = useState('')
  const [dirty, setDirty] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const card = useRef<HTMLElement>(null)
  // Arriving from the end-of-day reminder (?focus=myday): bring this card into view and put the cursor in its note.
  useEffect(() => {
    if (!focus || !card.current) return
    card.current.scrollIntoView({ block: 'center', behavior: 'smooth' })
    card.current.querySelector('textarea')?.focus({ preventScroll: true })
  }, [focus, loading])

  const load = useCallback((signal: AbortSignal, keepNote: boolean) => {
    setError('')
    dailyReportApi.mine(date, signal).then(result => {
      setDay(result)
      if (!keepNote) { setNote(result.note ?? ''); setDirty(false) }
    }).catch(failure => {
      if (!signal.aborted) setError(failure instanceof Error ? failure.message : 'Your report could not be loaded.')
    }).finally(() => { if (!signal.aborted) setLoading(false) })
  }, [date])

  // A new day starts clean; a live refresh re-reads the state but never throws away a note being typed.
  useEffect(() => { setDirty(false); setSaved(false); setLoading(true) }, [date])
  useEffect(() => {
    const controller = new AbortController()
    load(controller.signal, dirty)
    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, refreshKey])

  const submitted = !!day && (day.submitted ?? !!day.submittedAt)
  const allowed = canSubmitDay(date, today)
  async function submit() {
    setBusy(true); setError(''); setSaved(false)
    try {
      const result = await dailyReportApi.submit(date, note.trim() ? note.trim() : null)
      setDay({ ...result, submitted: true }); setNote(result.note ?? ''); setDirty(false); setSaved(true)
      onSubmitted()
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'The report could not be submitted. Please try again.')
    } finally { setBusy(false) }
  }
  const time = clockTime(day?.submittedAt, timeZone)
  return <section ref={card} id="rt-myday-card" className="dw-panel dwr-myday" aria-labelledby="rt-myday-title">
    <header className="dw-panel-head">
      <div><h2 id="rt-myday-title">My day</h2></div>
      <div className="dwr-seg is-small" role="group" aria-label="Which day">
        <button type="button" aria-pressed={which === 'today'} onClick={() => setWhich('today')}>Today</button>
        <button type="button" aria-pressed={which === 'yesterday'} onClick={() => setWhich('yesterday')}>Yesterday</button>
      </div>
    </header>
    <label className="dwr-note">
      <span>Note</span>
      <Textarea value={note} rows={2} maxRows={6} maxLength={NOTE_LIMIT} placeholder="Add a note" disabled={!allowed || busy}
        onChange={event => { setNote(event.target.value); setDirty(true); setSaved(false) }} />
    </label>
    <footer className="dwr-myday-foot">
      <p className={submitted ? 'dwr-state is-ok' : 'dwr-state'} role="status" aria-live="polite">
        {submitted
          ? <><Check size={14} strokeWidth={2.2} aria-hidden="true" />{saved ? 'Saved. ' : ''}Report submitted{time ? ` at ${time}` : ''}</>
          : allowed ? `Not submitted yet for ${which}.` : 'This day can no longer be submitted.'}
      </p>
      <Button variant="primary" size="md" disabled={busy || loading || !allowed} onClick={() => void submit()}
        icon={busy ? <Loader2 size={15} className="animate-spin" /> : undefined}>
        {busy ? 'Submitting…' : submitted ? 'Update note' : `Submit ${which === 'today' ? "today's" : "yesterday's"} report`}
      </Button>
    </footer>
    {error && <p className="dwr-error" role="alert">{error}</p>}
  </section>
}
