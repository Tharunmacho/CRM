import type { CSSProperties } from 'react'
import { File, FileImage, FileSpreadsheet, FileText, Inbox, LayoutGrid, Mail, Server, UserCheck } from 'lucide-react'
import type { MailProvider } from '@/shared/api/client'
import { cn } from '@/shared/lib/cn'
import { initials } from '@/shared/lib/format'

/** One of six soft hues per name, identical to RecordIdentity, so a person keeps one colour everywhere. */
export const hueOf = (name: string) => name.split('').reduce((total, character) => total + character.charCodeAt(0), 0) % 6

/** Initials for a person, or the first two letters of an address (info@naukri.com becomes IN). */
export const avatarText = (name: string) => {
  const text = name.trim()
  if (text.includes('@') && !text.includes(' ')) return text.split('@')[0]!.replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase() || '@'
  return initials(text) || '?'
}

/** Circular initials avatar with a deterministic hue (classes come from styles/depth.css). */
export function SenderAvatar({ name, className }: { name: string; className?: string }) {
  return <span className={cn('record-avatar mb-avatar', `avatar-hue-${hueOf(name)}`, className)} aria-hidden="true">{avatarText(name)}</span>
}

const PROVIDER_ICON = { gmail: Mail, microsoft: LayoutGrid, zoho: Inbox, imap: Server } as const

/** A small tinted disc that tells the provider apart at a glance. `all` is the unified-inbox mark. */
export function ProviderMark({ provider, size = 'md', off, title }: { provider: MailProvider | 'all'; size?: 'sm' | 'md'; off?: boolean; title?: string }) {
  const Icon = provider === 'all' ? Inbox : PROVIDER_ICON[provider]
  return <span className={cn('mb-prov', `is-${provider}`, size === 'sm' && 'is-sm', off && 'is-off')} title={title} aria-hidden={title ? undefined : true}><Icon size={size === 'sm' ? 10 : 15} strokeWidth={2} /></span>
}

export type FileKind = 'pdf' | 'doc' | 'sheet' | 'image' | 'other'
export const fileKind = (name: string): FileKind => {
  const extension = name.split('.').pop()?.toLowerCase() ?? ''
  if (extension === 'pdf') return 'pdf'
  if (['doc', 'docx', 'rtf', 'odt', 'txt'].includes(extension)) return 'doc'
  if (['xls', 'xlsx', 'csv', 'ods'].includes(extension)) return 'sheet'
  if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'heic', 'bmp', 'tif', 'tiff'].includes(extension)) return 'image'
  return 'other'
}

/** A file-type tile: red PDF, blue document, green sheet, violet image. */
export function FileIcon({ name, size = 'md' }: { name: string; size?: 'sm' | 'md' | 'lg' }) {
  const kind = fileKind(name)
  const Icon = kind === 'image' ? FileImage : kind === 'sheet' ? FileSpreadsheet : kind === 'other' ? File : FileText
  return <span className={cn('mb-file', `is-${kind}`, size === 'sm' && 'is-sm', size === 'lg' && 'is-lg')} aria-hidden="true"><Icon size={size === 'sm' ? 14 : size === 'lg' ? 24 : 17} strokeWidth={1.9} /></span>
}

/** Same tile under the name the attachment preview uses. */
export const FileTile = FileIcon

export const fileSize = (kb: number) => kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${kb} KB`

/** "Candidate created" chip that opens the candidate (or the message when several were created). */
export function CandidateChip({ label, disabled, title, ariaLabel, onClick, inline, style }: {
  label: string; disabled?: boolean; title: string; ariaLabel?: string; onClick: () => void; inline?: boolean; style?: CSSProperties
}) {
  return <button type="button" className={cn('mb-chip is-candidate', inline && 'is-inline')} disabled={disabled} title={title} aria-label={ariaLabel} style={style} onClick={onClick}><UserCheck size={13} strokeWidth={2} /><span>{label}</span></button>
}
