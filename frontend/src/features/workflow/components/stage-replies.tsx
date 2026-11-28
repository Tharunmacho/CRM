import { useState } from 'react'
import { ADVANCE_STAGE } from '@/shared/workflow/stages'
import type { AdvanceStatus } from '@/shared/workflow/advance'
import { Button, Input } from '@/shared/ui/primitives'

function readReplies(key: string): string[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(key) ?? '[]')
    return Array.isArray(saved) ? saved.filter((item): item is string => typeof item === 'string' && !!item.trim()) : []
  } catch {
    return []
  }
}

export function StageReplies({ userId, stage, disabled, status, onChoose }: {
  userId: string
  stage: number
  disabled: boolean
  status: unknown
  onChoose: (text: string, status?: AdvanceStatus) => void
}) {
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
    <legend className="mb-2 font-semibold">Quick replies</legend>
    <div className="sm-decision-row">
      {stage === ADVANCE_STAGE && <>
        <Button size="sm" aria-pressed={status === 'collected'} variant={status === 'collected' ? 'primary' : 'secondary'} onClick={() => onChoose('Advance payment received.', 'collected')}>Received</Button>
        <Button size="sm" aria-pressed={status === 'pending'} variant={status === 'pending' ? 'primary' : 'secondary'} onClick={() => onChoose('Advance payment not received.', 'pending')}>Not received</Button>
      </>}
      {replies.map(reply => <Button key={reply} size="sm" onClick={() => onChoose(reply)}>{reply}</Button>)}
      <Button size="sm" aria-expanded={editing} onClick={() => setEditing(!editing)}>{editing ? 'Done' : replies.length ? 'Manage replies' : 'Create a reply'}</Button>
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
