import { useState } from 'react'
import { cn } from '@/shared/lib/cn'
import { initials } from '@/shared/lib/format'
import { fileHref } from '@/shared/api/client'

/** The same deterministic soft hue the shared identity cells use, so a person keeps one colour everywhere. */
export const hueOf = (name: string) => name.split('').reduce((total, character) => total + character.charCodeAt(0), 0) % 6

/**
 * A circular avatar: the candidate's photo when one is on file, otherwise initials on a soft hue.
 * The photo sits over the initials, so a broken image falls back to them without a flash.
 */
export function PersonAvatar({ name, photoUrl, size = 40, className }: { name: string; photoUrl?: string | null; size?: number; className?: string }) {
  const [broken, setBroken] = useState(false)
  return (
    <span
      className={cn('record-avatar pa', `avatar-hue-${hueOf(name || '?')}`, className)}
      style={{ width: size, height: size, fontSize: Math.max(11, Math.round(size * 0.36)) }}
      aria-hidden="true"
    >
      {name ? initials(name) : '?'}
      {photoUrl && !broken && <img src={fileHref(photoUrl)} alt="" onError={() => setBroken(true)} />}
    </span>
  )
}
