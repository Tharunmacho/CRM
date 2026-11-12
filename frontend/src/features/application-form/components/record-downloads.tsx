import { useState } from 'react'
import { Download, FileSpreadsheet, Loader2 } from 'lucide-react'
import { Button } from '@/shared/ui/primitives'
import { downloadCandidateRecord } from '../inspection-api'

export function RecordDownloads({ candidateId, applicationId, recordName, dirty, canDownload }: {
  candidateId: string; applicationId?: string; recordName?: string; dirty: boolean; canDownload: boolean
}) {
  const [busy, setBusy] = useState<'xlsx' | 'zip' | null>(null)
  const [error, setError] = useState('')
  const download = async (kind: 'xlsx' | 'zip') => {
    setBusy(kind); setError('')
    try { await downloadCandidateRecord(candidateId, { applicationId }, kind, recordName) }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'The download failed. Please try again.') }
    finally { setBusy(null) }
  }
  return <div className="af-record-downloads">
    <div>
      <strong>Candidate records</strong>
      <p>{!canDownload ? 'Full record downloads are available to the assigned recruiter and managers.' : dirty ? 'Save your changes before downloading records.' : 'Excel includes saved details and the document checklist. The ZIP includes files and the Excel record.'}</p>
    </div>
    <div className="af-record-buttons">
      <Button disabled={dirty || busy !== null || !canDownload} icon={busy === 'xlsx' ? <Loader2 className="animate-spin" /> : <FileSpreadsheet />} onClick={() => void download('xlsx')}>Export Excel</Button>
      <Button disabled={dirty || busy !== null || !canDownload} icon={busy === 'zip' ? <Loader2 className="animate-spin" /> : <Download />} onClick={() => void download('zip')}>{busy === 'zip' ? 'Preparing files…' : 'Download files'}</Button>
    </div>
    {error && <p className="af-note is-error" role="alert">{error}</p>}
  </div>
}
