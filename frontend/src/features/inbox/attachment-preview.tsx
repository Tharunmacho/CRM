import { useEffect, useState } from 'react'
import { Download } from 'lucide-react'
import { fileHref } from '@/shared/api/client'
import { Button } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { FileTile } from './mail-parts'

export type PreviewFile = { name: string; url: string }

const OFFICE = ['doc', 'docx', 'odt', 'rtf', 'xls', 'xlsx', 'ods']

const kind = (name: string) => {
  const extension = name.split('.').pop()?.toLowerCase() ?? ''
  if (extension === 'pdf') return 'pdf'
  if (['jpg', 'jpeg', 'png', 'gif', 'webp'].includes(extension)) return 'image'
  if (OFFICE.includes(extension)) return 'office'
  return 'other'
}

type Converted =
  | { state: 'loading' }
  | { state: 'pdf'; src: string }
  | { state: 'html'; html: string }
  | { state: 'none'; reason: string }

/** Asks the server to turn a Word or Excel attachment into a PDF (or a simple page) the browser can show. */
function useOfficePreview(url: string, enabled: boolean): Converted {
  const [result, setResult] = useState<Converted>({ state: 'loading' })
  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    let objectUrl: string | null = null
    setResult({ state: 'loading' })
    ;(async () => {
      try {
        const response = await fetch(fileHref(`${url}/preview`), { credentials: 'include', signal: controller.signal })
        if (!response.ok) {
          let reason = 'We cannot show this file here.'
          try { reason = (await response.json())?.detail || reason } catch { /* keep the generic message */ }
          setResult({ state: 'none', reason: typeof reason === 'string' ? reason : 'We cannot show this file here.' })
          return
        }
        const type = response.headers.get('content-type') ?? ''
        if (type.startsWith('text/html')) {
          setResult({ state: 'html', html: await response.text() })
        } else {
          objectUrl = URL.createObjectURL(await response.blob())
          setResult({ state: 'pdf', src: objectUrl })
        }
      } catch (error) {
        if ((error as Error)?.name !== 'AbortError') setResult({ state: 'none', reason: 'We could not load the preview. Check your connection and try again, or download the file.' })
      }
    })()
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [url, enabled])
  return result
}

/** Shows an email attachment without leaving the inbox. Word and Excel files are converted by the server; other types offer a download. */
export function AttachmentPreview({ file, onClose }: { file: PreviewFile; onClose: () => void }) {
  const href = fileHref(file.url)
  const download = fileHref(`${file.url}${file.url.includes('?') ? '&' : '?'}download=true`)
  const type = kind(file.name)
  const office = useOfficePreview(file.url, type === 'office')
  const cannotShow = (reason: string) => <div className="ix-preview-none"><FileTile name={file.name} size="lg" /><p>{reason}</p><span>Download it to open it on your computer.</span></div>
  return <Dialog open onClose={onClose} title={file.name} description="Preview of the email attachment" width="xl" scroll="inside"
    footer={<><Button onClick={onClose}>Close</Button><a href={download}><Button variant="primary" icon={<Download size={16} />}>Download</Button></a></>}>
    <div className="ix-preview">
      {type === 'pdf' && <iframe title={file.name} src={href} className="ix-preview-frame" />}
      {type === 'image' && <img alt={file.name} src={href} className="ix-preview-image" />}
      {type === 'office' && office.state === 'loading' && <div className="ix-preview-none" role="status"><p>Preparing the preview...</p><span>Word and Excel files can take a few seconds.</span></div>}
      {type === 'office' && office.state === 'pdf' && <iframe title={file.name} src={office.src} className="ix-preview-frame" />}
      {type === 'office' && office.state === 'html' && <iframe title={file.name} srcDoc={office.html} sandbox="" className="ix-preview-frame" />}
      {type === 'office' && office.state === 'none' && cannotShow(office.reason)}
      {type === 'other' && cannotShow('We cannot show this type of file here.')}
    </div>
  </Dialog>
}
