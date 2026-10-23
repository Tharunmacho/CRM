import { Download } from 'lucide-react'
import { fileHref } from '@/shared/api/client'
import { Button } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'

export type PreviewFile = { name: string; url: string }

const kind = (name: string) => {
  const extension = name.split('.').pop()?.toLowerCase() ?? ''
  if (extension === 'pdf') return 'pdf'
  if (['jpg', 'jpeg', 'png', 'gif', 'webp'].includes(extension)) return 'image'
  return 'other'
}

/** Shows an email attachment without leaving the inbox. Other file types offer a download. */
export function AttachmentPreview({ file, onClose }: { file: PreviewFile; onClose: () => void }) {
  const href = fileHref(file.url)
  const download = fileHref(`${file.url}${file.url.includes('?') ? '&' : '?'}download=true`)
  const type = kind(file.name)
  return <Dialog open onClose={onClose} title={file.name} width="xl" scroll="inside"
    footer={<><Button onClick={onClose}>Close</Button><a href={download}><Button variant="primary" icon={<Download size={15} />}>Download</Button></a></>}>
    {type === 'pdf' && <iframe title={file.name} src={href} className="h-[70vh] w-full rounded-control border border-rule bg-white" />}
    {type === 'image' && <img alt={file.name} src={href} className="mx-auto max-h-[70vh] max-w-full rounded-control border border-rule bg-white object-contain" />}
    {type === 'other' && <p className="py-10 text-center text-sm text-ink-500">This file type cannot be previewed in the browser. Download it to open it.</p>}
  </Dialog>
}
