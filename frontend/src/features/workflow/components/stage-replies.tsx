import { useState } from 'react'
import { Button, Input } from '@/shared/ui/primitives'

type ReplyValue = string | boolean
interface ReplyPair { field: string; choices: [string, ReplyValue][] }
export const stageReplyPairs: Record<number, ReplyPair | undefined> = {
  4: { field: 'result', choices: [['Selected', 'selected'], ['Not Selected', 'rejected']] },
  5: { field: 'result', choices: [['Agreed', 'agreed'], ['Not Agreed', 'declined']] },
  6: { field: 'advanceStatus', choices: [['Received', 'collected'], ['Pending', 'pending']] },
  7: { field: 'collected', choices: [['Received', 'yes'], ['Pending', 'no']] },
  8: { field: 'result', choices: [['Fit', 'fit'], ['Unfit', 'unfit']] },
  9: { field: 'pccStatus', choices: [['Received', 'received'], ['Pending', 'pending']] },
  10: { field: 'arrived', choices: [['Received', 'yes'], ['Not Received', 'no']] },
  11: { field: 'googleReview', choices: [['Received', true], ['Pending', false]] },
  12: { field: 'done', choices: [['Received', 'yes'], ['Pending', 'no']] },
  13: { field: 'ticketStatus', choices: [['Received', 'received'], ['Pending', 'pending']] },
}

function readReplies(key: string): string[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(key) ?? '[]')
    return Array.isArray(saved) ? saved.filter((item): item is string => typeof item === 'string' && !!item.trim()) : []
  } catch {
    return []
  }
}

export function StageReplies({ userId, stage, disabled, status, feedback, onSelect, onChoose }: {
  userId: string
  stage: number
  disabled: boolean
  status: unknown
  feedback: unknown
  onSelect: (field: string, value: ReplyValue) => void
  onChoose: (text: string) => void
}) {
  const selectedLines = new Set(typeof feedback === 'string' ? feedback.split('\n').map(line => line.trim()) : [])
  const key = `crm.stageReplies.${userId}.${stage}`
  const [replies, setReplies] = useState(() => readReplies(key))
  const [editing, setEditing] = useState(false)
  const [label, setLabel] = useState('')
  const [error, setError] = useState('')

  function save(next: string[]) {
    try {
      localStorage.setItem(key, JSON.stringify(next))
      setReplies(next)
      setLabel('')
      setError('')
    } catch {
      setError('This browser could not save your quick replies.')
    }
  }

  function add() {
    const text = label.trim()
    if (!text) return
    const current = readReplies(key)
    if (current.some(reply => reply.toLowerCase() === text.toLowerCase())) {
      setError('That quick reply already exists.')
      return
    }
    save([...current, text])
  }

  return <fieldset className="mb-4 space-y-2" disabled={disabled}>
    <legend className="sr-only">Stage replies</legend>
    <div className="sm-decision-row sm-quick-replies">
      {stageReplyPairs[stage]?.choices.map(([text, value]) => <Button key={text} size="sm" aria-pressed={status === value}
        onClick={() => onSelect(stageReplyPairs[stage]!.field, value)}>{text}</Button>)}
      {replies.map(reply => <Button key={reply} size="sm" aria-pressed={selectedLines.has(reply)} onClick={() => onChoose(reply)}>{reply}</Button>)}
      <Button size="sm" aria-expanded={editing} onClick={() => setEditing(!editing)}>{editing ? 'Done' : 'Create reply'}</Button>
    </div>
    {editing && <div className="space-y-2">
      <label>New reply<Input value={label} maxLength={240} onChange={event => setLabel(event.target.value)} onKeyDown={event => {
        if (event.key === 'Enter') { event.preventDefault(); add() }
      }} /></label>
      <Button size="sm" disabled={!label.trim()} onClick={add}>Add reply</Button>
      {replies.map(reply => <div key={reply} className="flex items-center gap-2"><span>{reply}</span><Button size="sm" aria-label={`Remove ${reply}`} onClick={() => save(readReplies(key).filter(item => item !== reply))}>Remove</Button></div>)}
      {error && <p role="alert">{error}</p>}
    </div>}
  </fieldset>
}
