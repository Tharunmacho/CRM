import { useState, type CSSProperties } from 'react'
import { cn } from '@/shared/lib/cn'
import { initials } from '@/shared/lib/format'

/**
 * Avatar: a rounded square (never a circle) with initials on a hue taken from the name, or a photo in the same shape.
 *
 * Props
 *  - name   string   used for the initials and the hue
 *  - src?   string   photo URL; falls back to initials if it fails to load
 *  - size?  'xs' (24) | 'sm' (32) | 'md' (40, default) | 'lg' (48) | 'xl' (64)
 *  - kind?  'person' (default) | 'company'   company = squarer corner and a darker tint
 *  - label? string   when set the avatar is announced (role="img"); otherwise it is decorative
 */
const HUES = [222, 262, 162, 28, 340, 192, 46, 300]

export function nameHue(name: string) {
  return HUES[name.split('').reduce((total, character) => total + character.charCodeAt(0), 0) % HUES.length]
}

export function Avatar({ name, src, size = 'md', kind = 'person', label, className }: { name: string; src?: string | null; size?: 'xs' | 'sm' | 'md' | 'lg' | 'xl'; kind?: 'person' | 'company'; label?: string; className?: string }) {
  const [failed, setFailed] = useState(false)
  const a11y = label ? { role: 'img', 'aria-label': label } : { 'aria-hidden': true as const }
  return (
    <span className={cn('v2-avatar', `v2-avatar-${size}`, className)} data-kind={kind} style={{ '--av-h': nameHue(name || '?') } as CSSProperties} {...a11y}>
      {src && !failed ? <img src={src} alt="" loading="lazy" onError={() => setFailed(true)} /> : <span>{initials(name || '?') || '?'}</span>}
    </span>
  )
}
