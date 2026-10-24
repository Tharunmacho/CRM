import { useRef, useState } from 'react'
import { CloudUpload, FileText, Image as ImageIcon, LoaderCircle, Plus, X, type LucideIcon } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { DOCUMENT_ACCEPT, DOCUMENT_HELP, documentFileError } from '@/shared/lib/document-files'
import { FileLink } from '@/shared/ui/file-drop'

export interface SlotFile {
  id: string
  name: string
  sizeKb: number
  url?: string
  /** Short state shown beside the file, for example "Saved". */
  status?: { label: string; tone: 'good' | 'warn' | 'bad' | 'busy' | 'muted' }
}

const isImage = (name: string) => /\.(jpe?g|png|webp|heic|heif|tiff?)$/i.test(name)

/**
 * A document slot that takes several files (education and experience certificates): the files
 * chosen so far are listed one per row with their size and a Remove button, and "Add file"
 * (or dropping files here) adds more. Every file is uploaded on its own; the caller decides what
 * "uploaded" and "removed" mean.
 */
export function MultiFileSlot({ label, icon: Icon, help, files, max = 6, disabled, onAdd, onRemove }: {
  label: string
  icon: LucideIcon
  help?: string
  files: SlotFile[]
  max?: number
  disabled?: boolean
  onAdd: (files: File[]) => Promise<void>
  onRemove: (id: string) => Promise<void>
}) {
  const input = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [removing, setRemoving] = useState<string | null>(null)
  const [errors, setErrors] = useState<string[]>([])
  const locked = disabled || busy || removing !== null
  const room = Math.max(0, max - files.length)

  const take = async (list: FileList | null) => {
    if (locked || !list || list.length === 0) return
    const picked = [...list]
    const problems: string[] = []
    const good: File[] = []
    for (const file of picked) {
      const problem = documentFileError(file)
      if (problem) problems.push(`${file.name}: ${problem}`)
      else good.push(file)
    }
    if (good.length > room) {
      problems.push(`You can add up to ${max} files here. ${good.length - room} ${good.length - room === 1 ? 'file was' : 'files were'} not added.`)
      good.length = room
    }
    setErrors(problems)
    if (good.length === 0) return
    setBusy(true)
    try { await onAdd(good) }
    catch (error) { setErrors((previous) => [...previous, error instanceof Error ? error.message : 'We could not upload the file. Please try again.']) }
    finally { setBusy(false) }
  }

  const remove = async (file: SlotFile) => {
    if (locked) return
    setRemoving(file.id); setErrors([])
    try { await onRemove(file.id) }
    catch (error) { setErrors([error instanceof Error ? error.message : 'We could not remove the file. Please try again.']) }
    finally { setRemoving(null) }
  }

  return (
    <div
      className={cn('ac-multi', dragging && 'is-dragging', locked && 'is-locked')}
      aria-busy={busy}
      onDragOver={(event) => { event.preventDefault(); if (!locked && room > 0) setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => { event.preventDefault(); setDragging(false); void take(event.dataTransfer.files) }}
    >
      <div className="ac-multi-head">
        <span className={cn('ac-multi-icon', files.length > 0 && 'is-filled')} aria-hidden><Icon className="h-5 w-5" /></span>
        <div className="min-w-0 flex-1">
          <p className="ac-multi-title">{label}<span className="ac-multi-badge">{files.length > 0 ? `${files.length} added` : 'Optional'}</span></p>
          <p className="ac-multi-help">{busy ? 'Uploading…' : help || DOCUMENT_HELP}</p>
        </div>
      </div>

      {files.length > 0 && (
        <ul className="ac-multi-list" aria-label={`${label} added so far`}>
          {files.map((file) => {
            const FileIcon = isImage(file.name) ? ImageIcon : FileText
            return (
              <li key={file.id} className="ac-multi-item">
                <FileIcon size={16} className="ac-multi-file-icon" aria-hidden />
                <div className="ac-multi-item-main">
                  <p className="ac-multi-name">{file.url ? <FileLink url={file.url} className="hover:text-navy-700">{file.name}</FileLink> : file.name}</p>
                  <p className="ac-multi-meta tnum">{file.sizeKb} KB{file.status && <span className={cn('ac-multi-state', `is-${file.status.tone}`)}> · {file.status.label}</span>}</p>
                </div>
                {!disabled && (
                  <button type="button" className="ac-multi-remove" disabled={locked} onClick={() => void remove(file)} aria-label={`Remove ${file.name}`} title={`Remove ${file.name}`}>
                    {removing === file.id ? <LoaderCircle size={16} className="ac-spin" aria-hidden /> : <X size={16} aria-hidden />}
                    <span>Remove</span>
                  </button>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {!disabled && (
        <div className="ac-multi-foot">
          <button type="button" className="ui-file-row-button ac-multi-add" disabled={locked || room === 0} onClick={() => input.current?.click()}>
            {files.length > 0 ? <Plus className="h-4 w-4" aria-hidden /> : <CloudUpload className="h-4 w-4" aria-hidden />}
            {files.length > 0 ? 'Add another file' : 'Choose files'}
          </button>
          <span className="ac-multi-note">{room === 0 ? `You have reached the limit of ${max} files.` : 'You can pick several files at once, or drop them here.'}</span>
        </div>
      )}
      <input ref={input} type="file" multiple accept={DOCUMENT_ACCEPT} aria-label={`Choose ${label}`} className="sr-only" disabled={locked} tabIndex={-1}
        onChange={(event) => { const list = event.currentTarget.files; void take(list); event.currentTarget.value = '' }} />
      {errors.length > 0 && <ul role="alert" className="ac-multi-errors">{errors.map((message) => <li key={message}>{message}</li>)}</ul>}
    </div>
  )
}
