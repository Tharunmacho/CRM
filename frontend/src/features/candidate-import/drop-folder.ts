/**
 * Dragging a folder onto the screen. Browsers hand a dropped folder over as file-system "entries", not as a file list, so the folder is
 * walked here and every file gets the same `webkitRelativePath` the folder button gives ("Folder/Candidate/file.pdf"): `groupPicked`
 * then works on both in exactly the same way. Anything unexpected returns null and the caller falls back to the button.
 */

export interface FsReader { readEntries(success: (entries: FsEntry[]) => void, failure?: (error: unknown) => void): void }
export interface FsEntry {
  isFile: boolean
  isDirectory: boolean
  name: string
  file?(success: (file: File) => void, failure?: (error: unknown) => void): void
  createReader?(): FsReader
}

type Transfer = { items?: ArrayLike<{ kind?: string; webkitGetAsEntry?: () => unknown }> | null }

/** Must be called inside the drop event itself: the entries stop working once the event is over. */
export function entriesFromTransfer(transfer: Transfer | null | undefined): FsEntry[] {
  const items = transfer?.items ? Array.from(transfer.items) : []
  return items.flatMap((item) => {
    const entry = item.kind === 'file' || item.kind === undefined ? item.webkitGetAsEntry?.() : null
    return entry ? [entry as FsEntry] : []
  })
}

/** Gives a file the folder path it was found under (the property is read-only on a real File, so it is defined on the object). */
function withPath(file: File, path: string): File {
  try {
    Object.defineProperty(file, 'webkitRelativePath', { value: path, configurable: true })
  } catch {
    return file
  }
  return (file as File & { webkitRelativePath?: string }).webkitRelativePath === path ? file : file
}

const readAll = (reader: FsReader): Promise<FsEntry[]> => new Promise((resolve, reject) => {
  const all: FsEntry[] = []
  // A folder is handed over in batches (about 100 at a time): ask again until an empty batch comes back.
  const next = () => reader.readEntries((batch) => {
    if (batch.length === 0) resolve(all)
    else { all.push(...batch); next() }
  }, reject)
  next()
})

const readFile = (entry: FsEntry): Promise<File> => new Promise((resolve, reject) => {
  if (!entry.file) reject(new Error('Not a file'))
  else entry.file(resolve, reject)
})

async function walk(entry: FsEntry, prefix: string, out: File[]): Promise<void> {
  const path = `${prefix}/${entry.name}`
  if (entry.isFile) {
    out.push(withPath(await readFile(entry), path))
  } else if (entry.isDirectory && entry.createReader) {
    for (const child of await readAll(entry.createReader())) await walk(child, path, out)
  }
}

export interface Dropped {
  files: File[]
  /** Name shown as the picked folder. */
  root: string
}

/**
 * The files of what was dropped, as if the folder button had picked them. One dropped folder is the picked folder itself; several are
 * treated as the contents of one folder. Only loose files (no folder) give `null`: the caller tells the person to drop a folder.
 */
export async function collectDropped(entries: FsEntry[]): Promise<Dropped | null> {
  const folders = entries.filter((entry) => entry.isDirectory)
  if (folders.length === 0) return null
  const files: File[] = []
  if (folders.length === 1) {
    const [only] = folders
    for (const child of only.createReader ? await readAll(only.createReader()) : []) await walk(child, only.name, files)
    return { files, root: only.name }
  }
  for (const folder of folders) await walk(folder, 'Dropped folders', files)
  return { files, root: 'Dropped folders' }
}

/** True when the browser can hand a dropped folder over as entries (all current desktop browsers). */
export const canDropFolders = (): boolean => typeof DataTransferItem !== 'undefined' && 'webkitGetAsEntry' in DataTransferItem.prototype
