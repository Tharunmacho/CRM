import { useRef, useState, type ReactNode } from 'react'
import { FileText, History, Image as ImageIcon, Paperclip, Upload } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { dateTimeLabel } from '@/shared/lib/format'
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
}: {
  url: string
  children: ReactNode
  className?: string
  title?: string
}) {
  return (
    <a
      href={fileHref(url)}
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
  return /\.(jpe?g|png|webp)$/i.test(name) ? ImageIcon : FileText
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
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [showHistory, setShowHistory] = useState(false)

  // Works uncontrolled: a caller that only wants the raw File (because the record it
  // belongs to does not exist yet) still gets the chosen file shown back to them.
  const [picked, setPicked] = useState<StoredFile | null>(null)
  const shown = value !== undefined ? value : picked

  const take = (files: FileList | null) => {
    if (disabled) return
    const f = files?.[0]
    if (!f) return
    const chosen: StoredFile = { name: f.name, sizeKb: Math.max(1, Math.round(f.size / 1024)) }
    setPicked(chosen)
    onChange?.(chosen)
    void onFile?.(f)
  }

  const Icon = shown ? iconFor(shown.name) : Upload

  return (
    <div className="ui-file-drop">
      {label && <span className="mb-1.5 block text-xs font-medium text-ink-600">{label}</span>}

      {shown ? (
        <div className="flex items-center gap-2.5 rounded-control border border-rule bg-white px-3 py-2">
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
              {busy ? (
                <span className="text-amber-700"> · {busyLabel}</span>
              ) : shown.id ? (
                <span className="text-navy-700"> · saved</span>
              ) : (
                <span className="text-amber-700"> · not uploaded</span>
              )}
            </p>
          </div>
          {versions.length > 0 && (
            <IconButton label="Version history" onClick={() => setShowHistory((v) => !v)}>
              <History className="h-4 w-4" />
            </IconButton>
          )}
          {shown.url && (
            <FileLink url={shown.url} className="shrink-0 text-xs font-medium hover:no-underline">
              View
            </FileLink>
          )}
          {!disabled && (
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              className="shrink-0 text-xs font-medium text-navy-700 hover:text-navy-600"
            >
              Replace
            </button>
          )}
        </div>
      ) : (
        <button
          type="button"
          disabled={disabled}
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragging(false)
            take(e.dataTransfer.files)
          }}
          className={cn(
            'file-drop-target flex w-full items-center justify-center gap-2 rounded-control border border-dashed px-3 py-3.5 text-sm transition-colors',
            dragging
              ? 'border-navy-500 bg-navy-50 text-navy-700'
              : 'border-rule bg-field/40 text-ink-400 hover:border-ink-300 hover:text-ink-600',
            disabled && 'cursor-not-allowed opacity-60',
          )}
        >
          <Upload className="h-4 w-4" />
          Choose a file or drop it here
        </button>
      )}

      <input
        ref={inputRef}
        type="file"
        className="sr-only"
        disabled={disabled}
        onChange={(e) => take(e.target.files)}
        tabIndex={-1}
      />

      {help && <span className="mt-1 block text-micro text-ink-400">{help}</span>}

      {showHistory && versions.length > 0 && (
        <ul className="mt-2 divide-y divide-rule-soft rounded-control border border-rule-soft bg-field/40">
          {[...versions].reverse().map((v) => (
            <li key={v.id} className="flex items-center gap-2 px-3 py-1.5 text-micro text-ink-500">
              <Paperclip className="h-3 w-3 shrink-0" />
              <span className="tnum">v{v.version}</span>
              <FileLink url={v.url} className="truncate">
                {v.name}
              </FileLink>
              <span className="ml-auto shrink-0">
                {v.uploadedBy} · {dateTimeLabel(v.uploadedAt)}
              </span>
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
}: {
  label: string
  versions: FileVersion[]
  onUpload?: (file: File) => void | Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const pickRef = useRef<HTMLInputElement>(null)
  const latest = versions[versions.length - 1]
  const Icon = latest ? iconFor(latest.name) : Paperclip

  return (
    <div className="border-b border-rule-soft last:border-0">
      <div className="flex items-center gap-3 py-2.5">
        <Icon className={cn('h-4 w-4 shrink-0', latest ? 'text-navy-600' : 'text-ink-300')} />
        <div className="min-w-0 flex-1">
          <p className="text-sm text-ink">{label}</p>
          {latest ? (
            <p className="truncate text-micro text-ink-400">
              <FileLink url={latest.url} title={`Open ${latest.name}`}>
                {latest.name}
              </FileLink>{' '}
              · {latest.sizeKb} KB · {dateTimeLabel(latest.uploadedAt)}
            </p>
          ) : (
            <p className="text-micro text-ink-300">Not uploaded</p>
          )}
        </div>
        {versions.length > 1 && (
          <button
            onClick={() => setOpen((v) => !v)}
            className="shrink-0 text-micro text-ink-400 hover:text-ink tnum"
          >
            {versions.length} versions
          </button>
        )}
        {latest && (
          <FileLink url={latest.url} className="shrink-0 text-xs font-medium hover:no-underline">
            View
          </FileLink>
        )}
        {onUpload && (
          <>
            <button
              onClick={() => pickRef.current?.click()}
              disabled={busy}
              className="shrink-0 text-xs font-medium text-navy-700 hover:text-navy-600 disabled:opacity-50"
            >
              {busy ? 'Uploading…' : latest ? 'Replace' : 'Upload'}
            </button>
            <input
              ref={pickRef}
              type="file"
              className="sr-only"
              tabIndex={-1}
              onChange={async (e) => {
                const f = e.target.files?.[0]
                if (!f) return
                setBusy(true)
                try {
                  await onUpload(f)
                } finally {
                  setBusy(false)
                  e.target.value = ''
                }
              }}
            />
          </>
        )}
      </div>
      {open && (
        <ul className="mb-2 ml-7 space-y-1 border-l border-rule-soft pl-3">
          {[...versions].reverse().map((v) => (
            <li key={v.id} className="text-micro text-ink-400">
              <span className="tnum">v{v.version}</span> · <FileLink url={v.url}>{v.name}</FileLink> ·{' '}
              {v.uploadedBy} ·{' '}
              {dateTimeLabel(v.uploadedAt)}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
