import { memo } from 'react'
import { CircleHelp } from 'lucide-react'
import { avatarTone, initialsOf } from '../import-view'

/** A round avatar with the person's initials in one of eight colours; a grey question mark when no name could be read. */
export const Avatar = memo(function Avatar({ name, unknown = false }: { name: string; unknown?: boolean }) {
  if (unknown) return <span className="ic-avatar is-unknown" aria-hidden="true"><CircleHelp /></span>
  return <span className={`ic-avatar ic-av-${avatarTone(name)}`} aria-hidden="true">{initialsOf(name)}</span>
})
