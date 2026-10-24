import { initials } from '@/shared/lib/format'

/** One of six soft hues, picked from the name so a person keeps the same colour everywhere on this screen. */
export function hueIndex(name: string) {
  let hash = 0
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) >>> 0
  return hash % 6
}

/** A circular initials avatar with a deterministic soft hue (styles live in add-candidate-v2.css). */
export function EntryAvatar({ name, size = 'md', className = '' }: { name: string; size?: 'sm' | 'md' | 'lg'; className?: string }) {
  return <span aria-hidden className={`ac-avatar ac-avatar-${size} ac-hue-${hueIndex(name)} ${className}`}>{initials(name) || '?'}</span>
}
