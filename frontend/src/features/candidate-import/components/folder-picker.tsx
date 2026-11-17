import { useRef, useState, type DragEvent } from 'react'
import { Folder, FolderOpen, FolderUp, ScanSearch, Users } from 'lucide-react'
import { Button } from '@/shared/ui/primitives'
import { canDropFolders, collectDropped, entriesFromTransfer } from '../drop-folder'

interface Props {
  /** Something was picked or dropped: the files with their folder paths, and the folder's name. */
  onFiles: (files: FileList | File[], root?: string) => void
  /** The picked folder, if any: its name and a line counting what it holds. */
  rootName: string
  summary: string
  hasFolder: boolean
  noCandidates: boolean
  busy: boolean
}

/** Step 1: a drop zone with the folder button. A dropped folder is walked into the same files the button would give; anything else falls back to the button. */
export function FolderPicker({ onFiles, rootName, summary, hasFolder, noCandidates, busy }: Props) {
  const input = useRef<HTMLInputElement>(null)
  const depth = useRef(0)
  const [dragging, setDragging] = useState(false)
  const [note, setNote] = useState('')
  const droppable = canDropFolders()

  const over = (event: DragEvent) => {
    if (!droppable || busy) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'copy'
  }
  const enter = (event: DragEvent) => {
    if (!droppable || busy) return
    event.preventDefault()
    depth.current += 1
    setDragging(true)
  }
  const leave = () => {
    depth.current = Math.max(0, depth.current - 1)
    if (depth.current === 0) setDragging(false)
  }
  const drop = (event: DragEvent) => {
    if (!droppable || busy) return
    event.preventDefault()
    depth.current = 0
    setDragging(false)
    // The entries must be taken now: they stop working once this event is over.
    const entries = entriesFromTransfer(event.dataTransfer)
    void (async () => {
      try {
        const dropped = await collectDropped(entries)
        if (!dropped) setNote('That was a single file, not a folder. Drop the folder itself, or use "Choose a folder".')
        else if (dropped.files.length === 0) setNote('That folder is empty. Drop a folder that holds the documents.')
        else { setNote(''); onFiles(dropped.files, dropped.root) }
      } catch {
        setNote('The dropped folder could not be read. Use "Choose a folder" instead.')
      }
    })()
  }

  const hidden = (
    <input
      ref={input} type="file" multiple className="hidden" tabIndex={-1} aria-hidden="true"
      onChange={(e) => { setNote(''); onFiles(e.target.files ?? [], undefined); e.target.value = '' }}
      {...({ webkitdirectory: '', directory: '' } as object)}
    />
  )
  const handlers = { onDragEnter: enter, onDragOver: over, onDragLeave: leave, onDrop: drop }

  if (hasFolder) {
    return (
      <section className={`ic-folder-bar${dragging ? ' is-dragging' : ''}`} aria-label="Chosen folder" {...handlers}>
        {hidden}
        <span className="ic-folder-badge" aria-hidden="true"><Folder /></span>
        <div className="ic-folder-text">
          <strong title={rootName}>{rootName || 'Chosen folder'}</strong>
          <span>{noCandidates ? "No candidate folders were found. Pick the folder that contains the candidates' folders." : summary}</span>
          {note && <span className="ic-drop-note" role="alert">{note}</span>}
        </div>
        <Button icon={<FolderOpen />} onClick={() => input.current?.click()} disabled={busy}>Change folder</Button>
      </section>
    )
  }
  return (
    <section className={`ic-drop${dragging ? ' is-dragging' : ''}`} aria-label="Choose a folder" {...handlers}>
      {hidden}
      <span className="ic-drop-icon" aria-hidden="true"><FolderUp /></span>
      <h2>Choose a folder</h2>
      <p className="ic-drop-help">Pick the folder with your candidates' documents. We sort the files into one candidate each, and you check them before anything is saved.</p>
      <div className="ic-drop-actions">
        <Button variant="primary" icon={<FolderOpen />} onClick={() => input.current?.click()} disabled={busy}>Choose a folder</Button>
        {droppable && <span className="ic-drop-or">or drag a folder here</span>}
      </div>
      {note && <p className="ic-drop-note" role="alert">{note}</p>}
      <ul className="ic-layouts" aria-label="Ways to arrange the folder">
        <li><Folder aria-hidden="true" /><span><b>One folder per candidate</b>Each sub-folder holds one candidate's files. Its name does not matter: the candidate's name is read from the documents.</span></li>
        <li><Users aria-hidden="true" /><span><b>One folder, several people</b>File names start with each person's name.</span></li>
        <li><ScanSearch aria-hidden="true" /><span><b>Files with any name</b>We can read the pages here to find who is who.</span></li>
      </ul>
    </section>
  )
}
