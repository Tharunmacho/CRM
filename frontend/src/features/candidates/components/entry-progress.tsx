import { ArrowRight, Check, LoaderCircle, Sparkles } from 'lucide-react'
import type { entryCompleteness } from '../entry-completeness'

type Completeness = ReturnType<typeof entryCompleteness>

/**
 * The strip above the form card: how much of the application form is filled (the same 19 items as the saved form), a
 * quiet count per part, and one prompt for what to add next, such as the passport when only the resume was uploaded.
 */
export function EntryProgress({ completeness, reading, filledByDocuments, next, onJump, onUpload }: {
  completeness: Completeness
  reading: boolean
  filledByDocuments: number
  next: 'passport' | 'resume' | 'photo' | null
  onJump: (anchor: string) => void
  onUpload: (kind: 'passport' | 'resume' | 'photo') => void
}) {
  const { percent, filled, total, groups } = completeness
  const prompt = next === 'passport'
    ? { title: 'Only the resume is added', text: 'Upload the passport copy and we will fill in the passport details and check the name and date of birth.', action: 'Upload passport' }
    : next === 'resume'
      ? { title: 'Only the passport is added', text: 'Add the resume to fill in education, experience and skills.', action: 'Upload resume' }
      : next === 'photo'
        ? { title: 'One more thing', text: 'The passport-size photo is the only document still missing.', action: 'Add photo' }
        : null
  return (
    <section className="ac-progress-card" aria-label="Application form progress">
      <div className="ac-prog-main">
        <div className="ac-prog-score">
          <strong className="tnum">{percent}%</strong>
          <span>complete</span>
        </div>
        <div className="ac-prog-body">
          <div className="ac-prog-line">
            <span className="ac-prog-label">
              {reading ? <><LoaderCircle size={14} className="ac-spin" aria-hidden /> Reading your documents. Fields fill in when we are done.</>
                : filledByDocuments ? <><Sparkles size={14} aria-hidden /> {filledByDocuments} {filledByDocuments === 1 ? 'field was' : 'fields were'} filled from your documents. Anything you type is kept.</>
                : 'Start with the documents below, then check and complete the fields.'}
            </span>
            <span className="ac-prog-count tnum">{filled} of {total} details</span>
          </div>
          <div className="ac-prog-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} aria-label="Application form completeness"><i style={{ width: `${percent}%` }} /></div>
          <ul className="ac-prog-groups">
            {groups.map((group) => {
              const done = group.filled === group.total
              return (
                <li key={group.section}>
                  <button type="button" className={done ? 'is-done' : ''} onClick={() => onJump(`candidate-${group.section === 'personal' ? 'personal' : group.section}`)}>
                    {done ? <Check size={12} strokeWidth={3} aria-hidden /> : <i aria-hidden />}
                    {group.label}
                    <span className="tnum">{group.filled}/{group.total}</span>
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      </div>
      {prompt && !reading && (
        <div className="ac-prog-prompt">
          <div><strong>{prompt.title}</strong><span>{prompt.text}</span></div>
          <button type="button" onClick={() => onUpload(next as 'passport' | 'resume' | 'photo')}>{prompt.action}<ArrowRight size={14} aria-hidden /></button>
        </div>
      )}
    </section>
  )
}
