import { useState } from 'react'
import { Loader2, MessageSquareText, Send, TimerReset, Trash2 } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { ago, initials } from '@/shared/lib/format'
import { Button, IconButton, Select, Textarea } from '@/shared/ui/primitives'
import {
  FEEDBACK_KINDS,
  FEEDBACK_MAX_LENGTH,
  FEEDBACK_REASONS,
  STAGE_NAMES,
  canDeleteFeedback,
  feedbackKindLabel,
  feedbackReasonLabel,
  stageClockText,
  stageClockTone,
} from '../form-model'
import type { FeedbackData, FeedbackKind, FeedbackReason, StageClock } from '../types'
import type { FeedbackDraft } from '../form-api'
import { SectionHeader } from './section-header'

export interface FeedbackActions {
  busy: boolean
  error: string
  onSend: (note: FeedbackDraft) => Promise<boolean>
  onDelete: (feedbackId: string) => Promise<void>
}

/** The stage clock as a chip: "Interview scheduling, 2 days 13 hours, limit 2 days"; amber near the limit, red once past it. */
export function StageClockChip({ clock }: { clock: StageClock }) {
  const tone = stageClockTone(clock)
  return (
    <span className={cn('af-clock', `is-${tone}`)} title={tone === 'over' ? 'This stage has taken longer than the limit' : 'Time spent in the current stage'}>
      <TimerReset aria-hidden="true" />{stageClockText(clock)}
    </span>
  )
}

/** Write a note: what kind, why (optional), and the words. Used inline in the banner and in the notes panel. */
export function NoteComposer({ actions, defaultKind = 'update', onSent, autoFocus, compact }: { actions: FeedbackActions; defaultKind?: FeedbackKind; onSent?: () => void; autoFocus?: boolean; compact?: boolean }) {
  const [kind, setKind] = useState<FeedbackKind>(defaultKind)
  const [reason, setReason] = useState<FeedbackReason | ''>('')
  const [body, setBody] = useState('')
  const text = body.trim()
  const send = async () => {
    if (!text || actions.busy) return
    if (await actions.onSend({ kind, reason, body: text })) { setBody(''); setReason(''); onSent?.() }
  }
  return (
    <form className={cn('af-composer', compact && 'is-compact')} onSubmit={(event) => { event.preventDefault(); void send() }}>
      <div className="af-composer-kinds" role="radiogroup" aria-label="Kind of note">
        {FEEDBACK_KINDS.map((item) => (
          <button key={item.value} type="button" role="radio" aria-checked={kind === item.value} className={cn('af-kind', kind === item.value && 'is-on')} onClick={() => setKind(item.value)}>{item.label}</button>
        ))}
      </div>
      <div className="af-composer-row">
        <label className="af-composer-reason">
          <span>Reason</span>
          <Select value={reason} aria-label="Reason" onChange={(event) => setReason(event.target.value as FeedbackReason | '')}>
            <option value="">No reason chosen</option>
            {FEEDBACK_REASONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </Select>
        </label>
      </div>
      <div className="af-composer-text">
        <Textarea
          value={body}
          rows={compact ? 2 : 3}
          maxLength={FEEDBACK_MAX_LENGTH}
          autoFocus={autoFocus}
          placeholder="What is happening with this candidate, and what is needed next?"
          aria-label="Note"
          onChange={(event) => setBody(event.target.value)}
          onKeyDown={(event) => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); void send() } }}
        />
        <span className="af-composer-count tnum" aria-live="polite">{body.length} of {FEEDBACK_MAX_LENGTH}</span>
      </div>
      {actions.error && <p className="af-note is-error" role="alert">{actions.error}</p>}
      <div className="af-composer-foot">
        <Button type="submit" variant="primary" disabled={!text || actions.busy} icon={actions.busy ? <Loader2 className="animate-spin" /> : <Send />}>{actions.busy ? 'Sending' : 'Send note'}</Button>
      </div>
    </form>
  )
}

/**
 * Above the form: the stage clock, and when the stage has run past its limit with no explanation, a calm banner that asks
 * for one. "Tell us why" opens the composer right there.
 */
export function StageClockBar({ feedback, actions, canWrite }: { feedback: FeedbackData; actions: FeedbackActions; canWrite: boolean }) {
  const [open, setOpen] = useState(false)
  const clock = feedback.stageClock
  if (!feedback.supported || !clock) return null
  const asking = clock.needsReason && canWrite
  return (
    <div className="af-clockbar">
      <StageClockChip clock={clock} />
      {asking && (
        <div className="af-ask" role="status">
          <div className="af-ask-text">
            <b>This stage has taken longer than the limit.</b>
            <span>Tell us why.</span>
          </div>
          {!open && <Button size="sm" variant="primary" icon={<MessageSquareText />} onClick={() => setOpen(true)}>Tell us why</Button>}
          {open && <div className="af-ask-form"><NoteComposer actions={actions} defaultKind="delay" autoFocus compact onSent={() => setOpen(false)} /></div>}
        </div>
      )}
    </div>
  )
}

/**
 * The recruiter notes section of the working card: the composer and the timeline, newest first. Anyone who can work the candidate
 * always gets the composer, even when the server has no notes yet (an older server): saving then says so calmly and the typed
 * text stays in the box.
 */
export function NotesPanel({ feedback, actions, viewer, canWork }: { feedback: FeedbackData; actions: FeedbackActions; viewer: { id: string; role: string }; canWork: boolean }) {
  if (!feedback.supported && !canWork) return null
  const clock = feedback.stageClock
  const canWrite = feedback.supported ? feedback.canWrite : canWork
  return (
    <section className="af-sec af-notes" id="af-notes" aria-labelledby="af-notes-title">
      <SectionHeader icon={<MessageSquareText />} id="af-notes-title" title="Recruiter notes" chip={clock ? <StageClockChip clock={clock} /> : undefined} />
      {canWrite
        ? <NoteComposer actions={actions} />
        : <p className="af-notes-readonly">You can read the notes on this candidate. Only the owner, an administrator or a manager can write them.</p>}
      {feedback.items.length === 0 ? (
        <p className="af-notes-empty">No notes yet. When this candidate takes longer than expected, write the reason here.</p>
      ) : (
        <ol className="af-timeline">
          {feedback.items.map((item) => {
            const reason = item.reason ? feedbackReasonLabel(item.reason) : ''
            return (
              <li key={item.id} className={cn('af-note-item', `is-${item.kind}`)}>
                <span className="af-avatar" aria-hidden="true">{initials(item.authorName || '?')}</span>
                <div className="af-note-body">
                  <div className="af-note-meta">
                    <b>{item.authorName || 'Someone'}</b>
                    <span className={cn('af-kind-chip', `is-${item.kind}`)}>{feedbackKindLabel(item.kind)}</span>
                    {reason && <span className="af-note-reason">{reason}</span>}
                    {item.stage != null && STAGE_NAMES[item.stage] && <span className="af-note-stage">{STAGE_NAMES[item.stage]}</span>}
                    {item.createdAt && <time dateTime={item.createdAt} title={new Date(item.createdAt).toLocaleString()}>{ago(item.createdAt)}</time>}
                  </div>
                  <p className="af-note-text">{item.body}</p>
                </div>
                {canDeleteFeedback(item, viewer) && (
                  <IconButton label="Delete this note" tone="danger" onClick={() => { if (window.confirm('Delete this note?')) void actions.onDelete(item.id) }}><Trash2 className="h-4 w-4" /></IconButton>
                )}
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}
