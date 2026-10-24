import { useRef, useState, type ReactNode } from 'react'
import { CloudUpload, Download, FileText, History, Image as ImageIcon, Paperclip, Upload, type LucideIcon } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { dateTimeLabel } from '@/shared/lib/format'
import { DOCUMENT_ACCEPT, DOCUMENT_HELP, documentFileError, fileDownloadPath, newestFileVersions } from '@/shared/lib/document-files'
import { fileHref } from '@/shared/api/client'
import type { FileVersion } from '@/shared/model/types'
import { IconButton } from './primitives'

export interface StoredFile {
  name: string
  sizeKb: number
  /** Set once the file is on the server; absent means it only exists in the browser. */
  id?: string
  url?: string
  version?: number
}

/**
 * Opens a stored file in a new tab: PDFs and photos display, Word files download. It
 * stops the click there, so a link inside a clickable table row does not open the row.
 */
export function FileLink({
  url,
  children,
  className,
  title,
  download = false,
}: {
  url: string
  children: ReactNode
  className?: string
  title?: string
  download?: boolean
}) {
  return (
    <a
      href={fileHref(download ? fileDownloadPath(url) : url)}
      target="_blank"
      rel="noreferrer"
      title={title}
      onClick={(e) => e.stopPropagation()}
      className={cn('text-navy-700 hover:text-navy-600 hover:underline', className)}
    >
      {children}
    </a>
  )
}

function iconFor(name: string) {
  return /\.(jpe?g|png|webp|heic)$/i.test(name) ? ImageIcon : FileText
}

function DownloadLink({ file }: { file: StoredFile }) {
  if (!file.url) return null
  return (
    <FileLink url={file.url} download title={`Download ${file.name}`} className="shrink-0 rounded-control p-1 hover:bg-field">
      <Download className="h-4 w-4" aria-hidden />
      <span className="sr-only">Download {file.name}</span>
    </FileLink>
  )
}

/**
 * Upload control. Files are never overwritten — a replacement becomes version n+1
 * and the history stays reachable.
 */
export function FileDrop({
  label,
  value,
  versions = [],
  onChange,
  onFile,
  help,
  disabled,
  busy,
  busyLabel = 'uploading…',
  uploaderName,
  variant = 'drop',
  icon,
  optional,
}: {
  label?: string
  value?: StoredFile | null
  versions?: FileVersion[]
  onChange?: (f: StoredFile | null) => void
  /** Receives the chosen file so the caller can upload it. */
  onFile?: (file: File) => void | Promise<void>
  help?: string
  disabled?: boolean
  busy?: boolean
  busyLabel?: string
  uploaderName?: (id: string) => string
  /** 'row' is a compact line: icon, title, hint and a Choose file button. */
  variant?: 'drop' | 'row'
  icon?: LucideIcon
  optional?: boolean
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inFlight = useRef(false)

  // Works uncontrolled: a caller that only wants the raw File (because the record it
  // belongs to does not exist yet) still gets the chosen file shown back to them.
  const [picked, setPicked] = useState<StoredFile | null>(null)
  const shown = value !== undefined ? value : picked
  const locked = disabled || busy || working

  const take = async (files: FileList | null) => {
    if (locked || inFlight.current) return
    const f = files?.[0]
    if (!f) return
    const invalid = documentFileError(f)
    setError(invalid ?? null)
    if (invalid) return
    const previous = picked
    const chosen: StoredFile = { name: f.name, sizeKb: Math.max(1, Math.round(f.size / 1024)) }
    setPicked(chosen)
    inFlight.current = true
    setWorking(true)
    try {
      await onFile?.(f)
      onChange?.(chosen)
    } catch (err) {
      setPicked(previous)
      setError(err instanceof Error ? err.message : 'The file was not uploaded. Please try again.')
    } finally {
      inFlight.current = false
      setWorking(false)
    }
  }

  const Icon = shown ? iconFor(shown.name) : Upload

  if (variant === 'row') {
    const RowIcon = icon ?? (shown ? iconFor(shown.name) : FileText)
    const hint = help || DOCUMENT_HELP
    return (
      <div
        className={cn('ui-file-row', dragging && 'is-dragging', locked && 'is-locked')}
        aria-busy={busy || working}
        onDragOver={(e) => { e.preventDefault(); if (!locked && !shown) setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); if (!shown) void take(e.dataTransfer.files) }}
      >
        <div className="ui-file-row-main">
          <span className={cn('ui-file-row-icon', shown && 'is-filled')} aria-hidden><RowIcon className="h-5 w-5" /></span>
          <div className="min-w-0 flex-1">
            <p className="ui-file-row-title">
              {label}
              {optional && !shown && <span className="ui-file-row-badge">Optional</span>}
            </p>
            {shown ? (
              <p className="truncate text-[13px] tnum text-ink-500">
                {shown.url ? <FileLink url={shown.url} className="text-ink hover:text-navy-700">{shown.name}</FileLink> : shown.name}
                <span className="text-ink-400"> · {shown.sizeKb} KB{shown.version ? ` · v${shown.version}` : ''}</span>
                {busy || working ? <span className="text-amber-700"> · {busyLabel}</span>
                  : shown.id ? <span className="text-accent-700"> · saved</span>
                  : <span className="text-amber-700"> · selected, not yet saved</span>}
              </p>
            ) : (
              <p className="text-[13px] leading-5 text-ink-500">{busy || working ? busyLabel : hint}</p>
            )}
          </div>
          <div className="ui-file-row-actions">
            {shown && versions.length > 0 && (
              <IconButton type="button" label="Version history" aria-expanded={showHistory} onClick={() => setShowHistory((v) => !v)}>
                <History className="h-4 w-4" />
              </IconButton>
            )}
            {shown?.url && <FileLink url={shown.url} className="text-xs font-medium hover:no-underline">View</FileLink>}
            {shown && <DownloadLink file={shown} />}
            {!disabled && (
              <button type="button" disabled={locked} onClick={() => inputRef.current?.click()} className="ui-file-row-button">
                {!shown && <CloudUpload className="h-4 w-4" aria-hidden />}
                {shown ? 'Replace' : 'Choose file'}
              </button>
            )}
          </div>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept={DOCUMENT_ACCEPT}
          aria-label={label ? `Choose ${label}` : 'Choose a document'}
          className="sr-only"
          disabled={locked}
          onChange={(e) => { void take(e.currentTarget.files); e.currentTarget.value = '' }}
          tabIndex={-1}
        />
        {error && <p role="alert" className="mt-1.5 text-xs text-stamp-700">{error}</p>}
        {showHistory && versions.length > 0 && (
          <ul className="mt-2 divide-y divide-rule-soft rounded-control border border-rule-soft bg-field/40">
            {newestFileVersions(versions).map((v) => (
              <li key={v.id} className="flex flex-wrap items-center gap-2 px-3 py-1.5 text-micro text-ink-500">
                <Paperclip className="h-3 w-3 shrink-0" />
                <span className="tnum">v{v.version}</span>
                <FileLink url={v.url} className="truncate">{v.name}</FileLink>
                <span className="ml-auto">{uploaderName?.(v.uploadedBy) || v.uploadedBy || 'Unknown uploader'} · {dateTimeLabel(v.uploadedAt)}</span>
                <DownloadLink file={v} />
              </li>
            ))}
          </ul>
        )}
      </div>
    )
  }

  return (
    <div className="ui-file-drop" aria-busy={busy || working}>
      {label && <span className="mb-1.5 block text-xs font-medium text-ink-600">{label}</span>}

      {shown ? (
        <div className="flex flex-wrap items-center gap-2.5 rounded-control border border-rule bg-white px-3 py-2">
          <Icon className="h-4 w-4 shrink-0 text-navy-600" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm text-ink">
              {shown.url ? (
                <FileLink url={shown.url} className="text-ink hover:text-navy-700">
                  {shown.name}
                </FileLink>
              ) : (
                shown.name
              )}
            </p>
            <p className="text-micro tnum">
              <span className="text-ink-400">{shown.sizeKb} KB</span>
              {shown.version ? <span className="text-ink-400"> · v{shown.version}</span> : null}
              {busy || working ? (
                <span className="text-amber-700"> · {busyLabel}</span>
              ) : shown.id ? (
                <span className="text-accent-700"> · saved</span>
              ) : (
                <span className="text-amber-700"> · selected, not yet saved</span>
              )}
            </p>
          </div>
          {versions.length > 0 && (
            <IconButton type="button" label="Version history" aria-expanded={showHistory} onClick={() => setShowHistory((v) => !v)}>
              <History className="h-4 w-4" />
            </IconButton>
          )}
          {shown.url && (
            <FileLink url={shown.url} className="shrink-0 text-xs font-medium hover:no-underline">
              View
            </FileLink>
          )}
          <DownloadLink file={shown} />
          {!disabled && (
            <button
              type="button"
              disabled={locked}
              onClick={() => inputRef.current?.click()}
              className="shrink-0 text-xs font-medium text-navy-700 hover:text-navy-600 disabled:opacity-50"
            >
              Replace
            </button>
          )}
        </div>
      ) : (
        <button
          type="button"
          disabled={locked}
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault()
            if (!locked) setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragging(false)
            void take(e.dataTransfer.files)
          }}
          className={cn(
            'file-drop-target flex w-full items-center justify-center gap-2 rounded-control border border-dashed px-3 py-3.5 text-[14px] transition-colors',
            dragging
              ? 'border-navy-600 bg-navy-50 text-ink'
              : 'border-[color:var(--border-control)] bg-white text-ink-500 hover:border-navy-600 hover:text-ink',
            locked && 'cursor-not-allowed opacity-60',
          )}
        >
          <Upload className="h-4 w-4" />
          {busy || working ? busyLabel : 'Choose a file or drop it here'}
        </button>
      )}

      <input
        ref={inputRef}
        type="file"
        accept={DOCUMENT_ACCEPT}
        aria-label={label ? `Choose ${label}` : 'Choose a document'}
        className="sr-only"
        disabled={locked}
        onChange={(e) => { void take(e.currentTarget.files); e.currentTarget.value = '' }}
        tabIndex={-1}
      />

      {(help || !disabled) && <span className="mt-1 block text-micro text-ink-400">{help}{help && !disabled ? ' ' : ''}{!disabled && DOCUMENT_HELP}</span>}
      {error && <p role="alert" className="mt-1.5 text-xs text-stamp-700">{error}</p>}

      {showHistory && versions.length > 0 && (
        <ul className="mt-2 divide-y divide-rule-soft rounded-control border border-rule-soft bg-field/40">
          {newestFileVersions(versions).map((v) => (
            <li key={v.id} className="flex flex-wrap items-center gap-2 px-3 py-1.5 text-micro text-ink-500">
              <Paperclip className="h-3 w-3 shrink-0" />
              <span className="tnum">v{v.version}</span>
              <FileLink url={v.url} className="truncate">
                {v.name}
              </FileLink>
              <span className="ml-auto">
                {uploaderName?.(v.uploadedBy) || v.uploadedBy || 'Unknown uploader'} · {dateTimeLabel(v.uploadedAt)}
              </span>
              <DownloadLink file={v} />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Read-only list of a document slot's versions, used on the candidate profile. */
export function DocumentSlotRow({
  label,
  versions,
  onUpload,
  uploaderName,
  emptyLabel = 'Not uploaded',
}: {
  label: string
  versions: FileVersion[]
  onUpload?: (file: File) => void | Promise<void>
  uploaderName?: (id: string) => string
  emptyLabel?: string
}) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inFlight = useRef(false)
  const pickRef = useRef<HTMLInputElement>(null)
  const latest = newestFileVersions(versions)[0]
  const Icon = latest ? iconFor(latest.name) : Paperclip

  return (
    <div className="border-b border-rule-soft last:border-0" aria-busy={busy}>
      <div className="flex flex-wrap items-center gap-2.5 py-2.5">
        <Icon className={cn('h-4 w-4 shrink-0', latest ? 'text-navy-600' : 'text-ink-300')} />
        <div className="min-w-0 flex-1">
          <p className="text-sm text-ink">{label}</p>
          {latest ? (
            <p className="truncate text-micro text-ink-400">
              <FileLink url={latest.url} title={`Open ${latest.name}`}>
                {latest.name}
              </FileLink>{' '}
              · v{latest.version} · {latest.sizeKb} KB
            </p>
          ) : (
            <p className="text-micro text-ink-400">{emptyLabel}</p>
          )}
        </div>
        {versions.length > 0 && (
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            className="shrink-0 text-micro text-ink-400 hover:text-ink tnum"
          >
            {open ? 'Hide history' : `History (${versions.length})`}
          </button>
        )}
        {latest && (
          <FileLink url={latest.url} className="shrink-0 text-xs font-medium hover:no-underline">
            View
          </FileLink>
        )}
        {latest && <DownloadLink file={latest} />}
        {onUpload && (
          <>
            <button
              type="button"
              onClick={() => pickRef.current?.click()}
              disabled={busy}
              className="shrink-0 text-xs font-medium text-navy-700 hover:text-navy-600 disabled:opacity-50"
            >
              {busy ? 'Uploading…' : latest ? 'Replace' : 'Upload'}
            </button>
            <input
              ref={pickRef}
              type="file"
              accept={DOCUMENT_ACCEPT}
              aria-label={`Choose ${label}`}
              disabled={busy}
              className="sr-only"
              tabIndex={-1}
              onChange={async (e) => {
                const f = e.currentTarget.files?.[0]
                e.currentTarget.value = ''
                if (!f || inFlight.current) return
                const invalid = documentFileError(f)
                setError(invalid ?? null)
                if (invalid) return
                inFlight.current = true
                setBusy(true)
                try {
                  await onUpload(f)
                } catch (err) {
                  setError(err instanceof Error ? err.message : 'The file was not uploaded. Please try again.')
                } finally {
                  inFlight.current = false
                  setBusy(false)
                }
              }}
            />
          </>
        )}
      </div>
      {error && <p role="alert" className="mb-2 ml-7 text-xs text-stamp-700">{error}</p>}
      {open && (
        <ul className="mb-2 ml-7 space-y-1 border-l border-rule-soft pl-3">
          {newestFileVersions(versions).map((v, index) => (
            <li key={v.id} className="flex items-start gap-2 text-micro text-ink-400">
              <div className="min-w-0 flex-1">
                <p className="break-words"><span className="tnum">v{v.version}{index === 0 ? ' · Latest' : ''}</span> · <FileLink url={v.url}>{v.name}</FileLink></p>
                <p>{uploaderName?.(v.uploadedBy) || v.uploadedBy || 'Unknown uploader'} · {dateTimeLabel(v.uploadedAt)} · {v.sizeKb} KB</p>
              </div>
              <DownloadLink file={v} />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
