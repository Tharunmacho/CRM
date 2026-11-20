import { useState } from 'react'
import { Loader2, MessageSquareText, Pencil, Save, Send, TimerReset, Trash2, X } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { ago, initials } from '@/shared/lib/format'
import { Button, IconButton, Select, Textarea } from '@/shared/ui/primitives'
import {
  FEEDBACK_KINDS,
  FEEDBACK_MAX_LENGTH,
  FEEDBACK_REASONS,
  STAGE_NAMES,
  canDeleteFeedback,
  canEditFeedback,
  feedbackKindLabel,
  feedbackReasonLabel,
  stageClockText,
  stageClockTone,
} from '../form-model'
import type { FeedbackData, FeedbackItem, FeedbackKind, FeedbackReason, StageClock } from '../types'
import type { FeedbackDraft } from '../form-api'
import { SectionHeader } from './section-header'
import { ConfirmRemoveDialog } from './confirm-remove'

export interface FeedbackActions {
  busy: boolean
  error: string
  onSend: (note: FeedbackDraft) => Promise<boolean>
  /** Save the new words of a note; true when saved. */
  onEdit: (feedbackId: string, body: string) => Promise<boolean>
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
          maxRows={8}
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
 * Above the form: when the stage has run past its limit with no explanation, a calm banner that asks for one ("Tell us why" opens
 * the composer right there). The clock itself is a labelled item of the context strip, not a chip floating above it.
 */
export function StageClockBar({ feedback, actions, canWrite }: { feedback: FeedbackData; actions: FeedbackActions; canWrite: boolean }) {
  const [open, setOpen] = useState(false)
  const clock = feedback.stageClock
  if (!feedback.supported || !clock) return null
  const asking = clock.needsReason && canWrite
  if (!asking) return null
  return (
    <div className="af-clockbar">
      <div className="af-ask" role="status">
        <div className="af-ask-text">
          <b>This stage has taken longer than the limit.</b>
          <span>Tell us why.</span>
        </div>
        {!open && <Button size="sm" variant="primary" icon={<MessageSquareText />} onClick={() => setOpen(true)}>Tell us why</Button>}
        {open && <div className="af-ask-form"><NoteComposer actions={actions} defaultKind="delay" autoFocus compact onSent={() => setOpen(false)} /></div>}
      </div>
    </div>
  )
}

/** One note on the timeline. Its author, the candidate's recruiter, an admin and an MD can edit (inline, Save / Cancel) or remove it, at any time. */
function NoteItem({ item, actions, viewer }: { item: FeedbackItem; actions: FeedbackActions; viewer: { id: string; role: string } }) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(item.body)
  const [removing, setRemoving] = useState(false)
  const reason = item.reason ? feedbackReasonLabel(item.reason) : ''
  const canEdit = canEditFeedback(item, viewer)
  const canDelete = canDeleteFeedback(item, viewer)
  const trimmed = text.trim()
  const save = async () => {
    if (!trimmed || actions.busy) return
    if (trimmed === item.body.trim()) { setEditing(false); return }
    if (await actions.onEdit(item.id, trimmed)) setEditing(false)
  }
  return (
    <li className={cn('af-note-item', `is-${item.kind}`)}>
      <span className="af-avatar" aria-hidden="true">{initials(item.authorName || '?')}</span>
      <div className="af-note-body">
        <div className="af-note-meta">
          <b>{item.authorName || 'Someone'}</b>
          <span className={cn('af-kind-chip', `is-${item.kind}`)}>{feedbackKindLabel(item.kind)}</span>
          {reason && <span className="af-note-reason">{reason}</span>}
          {item.stage != null && STAGE_NAMES[item.stage] && <span className="af-note-stage">{STAGE_NAMES[item.stage]}</span>}
          {item.createdAt && <time dateTime={item.createdAt} title={new Date(item.createdAt).toLocaleString()}>{ago(item.createdAt)}</time>}
          {item.editedAt && <span className="af-note-edited" title={`Edited ${new Date(item.editedAt).toLocaleString()}`}>edited {ago(item.editedAt)}</span>}
        </div>
        {editing ? (
          <div className="af-note-edit">
            <Textarea value={text} rows={3} maxRows={8} maxLength={FEEDBACK_MAX_LENGTH} autoFocus aria-label="Edit note" onChange={(event) => setText(event.target.value)}
              onKeyDown={(event) => { if (event.key === 'Escape') { event.preventDefault(); setEditing(false); setText(item.body) } if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); void save() } }} />
            {actions.error && <p className="af-note is-error" role="alert">{actions.error}</p>}
            <div className="af-note-edit-foot">
              <span className="af-composer-count tnum" aria-live="polite">{text.length} of {FEEDBACK_MAX_LENGTH}</span>
              <Button size="sm" variant="secondary" disabled={actions.busy} icon={<X />} onClick={() => { setEditing(false); setText(item.body) }}>Cancel</Button>
              <Button size="sm" variant="primary" disabled={!trimmed || actions.busy} icon={actions.busy ? <Loader2 className="animate-spin" /> : <Save />} onClick={() => void save()}>{actions.busy ? 'Saving' : 'Save'}</Button>
            </div>
          </div>
        ) : <p className="af-note-text">{item.body}</p>}
      </div>
      {!editing && (canEdit || canDelete) && (
        <span className="af-note-actions">
          {canEdit && <IconButton label="Edit this note" onClick={() => { setText(item.body); setEditing(true) }}><Pencil className="h-4 w-4" /></IconButton>}
          {canDelete && <IconButton label="Delete this note" tone="danger" onClick={() => setRemoving(true)}><Trash2 className="h-4 w-4" /></IconButton>}
        </span>
      )}
      {removing && <ConfirmRemoveDialog title="Delete this note?" description="The note disappears from the form. The audit trail keeps its text, who deleted it and when."
        detail={item.body.length > 160 ? `${item.body.slice(0, 160)}…` : item.body} confirmLabel="Delete note" onClose={() => setRemoving(false)} onConfirm={() => actions.onDelete(item.id)} />}
    </li>
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
        : <p className="af-notes-readonly">You can read the notes on this candidate. Only the owner, an admin or an MD can write them.</p>}
      {feedback.items.length === 0 ? (
        <p className="af-notes-empty">No notes yet. When this candidate takes longer than expected, write the reason here.</p>
      ) : (
        <ol className="af-timeline">
          {feedback.items.map((item) => <NoteItem key={item.id} item={item} actions={actions} viewer={viewer} />)}
        </ol>
      )}
    </section>
  )
}
