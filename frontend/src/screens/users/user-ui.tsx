import { useEffect, useState, type ReactNode } from 'react'
import { Check, Copy, Eye, EyeOff, ShieldCheck } from 'lucide-react'
import type { Role } from '@/shared/model/types'
import { cn } from '@/shared/lib/cn'
import { Badge } from '@/shared/ui/display'
import { Button } from '@/shared/ui/primitives'

export const roleLabels: Record<Role, string> = { admin: 'Admin', manager: 'Manager', staff: 'Staff' }
export const roleBlurbs: Record<Role, string> = {
  admin: 'Controls all settings and all accounts.',
  manager: 'Looks after staff, moves work between people and is told about very late tasks.',
  staff: 'Recruiter. Gets new candidates and does the tasks.',
}

export const failureText = (error: unknown) =>
  error instanceof Error ? error.message : 'We could not save that. Try again.'

export function initialsOf(name: string) {
  const parts = name.replace(/\(.*?\)/g, '').trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return '?'
  return ((parts[0][0] ?? '') + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase()
}

/** A circle with a soft deterministic hue per name (same hash as the shared RecordIdentity). */
export function UserAvatar({ name, size = 'md', muted }: { name: string; size?: 'sm' | 'md' | 'lg'; muted?: boolean }) {
  const hue = name.split('').reduce((total, character) => total + character.charCodeAt(0), 0) % 6
  return <span className={cn('record-avatar uv-avatar', `avatar-hue-${hue}`, size === 'lg' && 'is-lg', size === 'sm' && 'is-sm', muted && 'is-muted')} aria-hidden="true">{initialsOf(name)}</span>
}

/** Admin carries the primary colour, Manager a violet tint, Staff a quiet neutral; all are soft pills with a dot. */
export function RolePill({ role }: { role: Role }) {
  return <Badge variant="dot" tone={role === 'admin' ? 'navy' : 'neutral'} className={cn('uv-role', `is-${role}`)}>{roleLabels[role]}</Badge>
}

/** Sectioned-form block used by the dialogs: a soft icon circle, a title and a one-line hint. */
export function FormSection({ icon, title, hint, children, columns = true }: {
  icon: ReactNode; title: string; hint?: string; children: ReactNode; columns?: boolean
}) {
  return (
    <section className="uv-fs">
      <header>
        <span aria-hidden="true">{icon}</span>
        <div><h3>{title}</h3>{hint && <p>{hint}</p>}</div>
      </header>
      <div className={columns ? 'uv-fs-grid is-two' : 'uv-fs-grid'}>{children}</div>
    </section>
  )
}

/** Downloads rows already on screen as a CSV file (no request is made). */
export function downloadRows(filename: string, header: string[], rows: (string | number)[][]) {
  const cell = (value: string | number) => {
    const text = String(value ?? '')
    return /[",\r\n]/.test(text) ? '"' + text.replaceAll('"', '""') + '"' : text
  }
  const body = [header, ...rows].map((row) => row.map(cell).join(',')).join('\r\n')
  const url = URL.createObjectURL(new Blob(['﻿' + body], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

const LOWER = 'abcdefghijkmnopqrstuvwxyz'
const UPPER = 'ABCDEFGHJKLMNPQRSTUVWXYZ'
const DIGITS = '23456789'
const SYMBOLS = '!#$%*+-=?@'

/** 16 characters from an unambiguous alphabet using the browser CSPRNG; always mixes all four classes. */
export function generatePassword(length = 16) {
  const pools = [LOWER, UPPER, DIGITS, SYMBOLS]
  const all = pools.join('')
  const random = (max: number) => {
    const limit = Math.floor(0x100000000 / max) * max
    const buffer = new Uint32Array(1)
    do { crypto.getRandomValues(buffer) } while (buffer[0] >= limit)
    return buffer[0] % max
  }
  const chars = pools.map((pool) => pool[random(pool.length)])
  while (chars.length < length) chars.push(all[random(all.length)])
  for (let i = chars.length - 1; i > 0; i--) { const j = random(i + 1); [chars[i], chars[j]] = [chars[j], chars[i]] }
  return chars.join('')
}

/** Password box with reveal and copy. The value only ever lives in component state. */
export function PasswordReveal({ value, defaultVisible = false }: { value: string; defaultVisible?: boolean }) {
  const [visible, setVisible] = useState(defaultVisible)
  const [copied, setCopied] = useState(false)
  useEffect(() => { setCopied(false) }, [value])
  const copy = async () => {
    try { await navigator.clipboard.writeText(value); setCopied(true) }
    catch { setVisible(true) }
  }
  return (
    <div className="uv-secret">
      <code aria-label="Temporary password" data-testid="temporary-password">{visible ? value : '•'.repeat(Math.min(value.length, 16))}</code>
      <Button size="sm" variant="ghost" aria-label={visible ? 'Hide password' : 'Show password'} icon={visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />} onClick={() => setVisible((v) => !v)}>{visible ? 'Hide' : 'Show'}</Button>
      <Button size="sm" icon={copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />} onClick={() => void copy()}>{copied ? 'Copied' : 'Copy'}</Button>
    </div>
  )
}

/** Shown once, right after a generated password is accepted by the server. */
export function PasswordOnce({ name, email, password, created }: { name: string; email: string; password: string; created?: boolean }) {
  return (
    <div className="uv-once" role="status">
      <span className="uv-once-icon" aria-hidden="true"><ShieldCheck size={22} /></span>
      <h3>{created ? 'Account created' : 'Password reset'} for {name}</h3>
      <p>{email}</p>
      <PasswordReveal value={password} defaultVisible />
      <p className="uv-once-note">This temporary password is shown only once. It is not written in the history. Copy it and give it to the person in a safe way. Once you close this window, you cannot see it again.</p>
    </div>
  )
}
