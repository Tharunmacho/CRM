import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, PhoneCall, Link2, Pencil } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { dateTimeLabel, smartTime } from '@/shared/lib/format'
import { DOCUMENT_HELP } from '@/shared/lib/document-files'
import { useStore } from '@/shared/state/crm-store'
import { api } from '@/shared/api/client'
import { closureLabels, stageByNo } from '@/shared/workflow/stages'
import {
  Badge,
  EmptyState,
  PageHeader,
  Panel,
  PanelHeader,
  StageChip,
  StatusPill,
} from '@/shared/ui/display'
import { Button, Field, RadioRow, Textarea } from '@/shared/ui/primitives'
import { DocumentSlotRow } from '@/shared/ui/file-drop'
import { Dialog } from '@/shared/ui/overlay'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { MapToOrderDialog } from '@/features/candidates/components/map-dialog'
import { CandidateSections, type CandidateOcr } from '@/features/candidates/components/profile-sections'
import { CandidateEditDialog } from '@/features/candidates/components/candidate-edit-dialog'
import { CandidateAssignmentHistory } from '@/features/candidates/components/assignment-history'
import { EmailEvidenceHistory } from '@/features/inbox/email-evidence-history'

export default function CandidateProfilePage() {
  const { id } = useParams()
  const store = useStore()
  const navigate = useNavigate()
  const cand = store.candidates.find((c) => c.id === id)

  const [openApp, setOpenApp] = useState<string | null>(null)
  const [calling, setCalling] = useState(false)
  const [mapping, setMapping] = useState(false)
  const [editing, setEditing] = useState(false)

  // The stored OCR output is not in the bootstrap, so the profile fetches it for itself.
  const { notify } = store
  const [ocr, setOcr] = useState<CandidateOcr>({})
  useEffect(() => {
    if (!id) return
    let live = true
    setOcr({})
    api
      .get<CandidateOcr>(`/candidates/${id}/ocr`)
      .then((data) => live && setOcr(data ?? {}))
      .catch(() => live && notify('The resume details could not be loaded', 'warn'))
    return () => {
      live = false
    }
  }, [id, notify])

  if (!cand) {
    return (
      <EmptyState
        title="That candidate does not exist"
        description="They may have been merged into another record."
        action={<Button onClick={() => navigate('/data-bank')}>Back to Data Bank</Button>}
      />
    )
  }

  const owner = store.user(cand.ownerId)
  const apps = store.applicationsFor(cand.id)
  const active = apps.find((a) => a.status === 'active')
  const deployed = apps.some((a) => a.status === 'deployed') || cand.status === 'deployed'
  const canWork = store.can('work_any_candidate') || cand.ownerId === store.me.id

  return (
    <>
      <Link
        to="/data-bank"
        className="mb-3 inline-flex items-center gap-1.5 text-xs text-ink-500 hover:text-ink"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        Data Bank
      </Link>

      <PageHeader
        title={cand.name}
        description={
          [
            cand.designations.join(', ') || 'No designation recorded',
            cand.totalExperience > 0 && `${cand.totalExperience} years experience`,
            cand.currentCity,
          ]
            .filter(Boolean)
            .join(' · ')
        }
        actions={
          <>
            {canWork && <Button icon={<Pencil size={14} />} onClick={() => setEditing(true)}>Correct profile</Button>}
            {!active && !deployed && (
              <Button variant="primary" icon={<Link2 className="h-4 w-4" />} onClick={() => setMapping(true)}>
                Map to an order
              </Button>
            )}
            {active && (
              <Button variant="primary" onClick={() => setOpenApp(active.id)}>
                Open stage {active.currentStage}
              </Button>
            )}
          </>
        }
      >
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <StatusPill status={cand.status} />
          {cand.needsReview && <Badge tone="amber">Needs review — check fields on the call</Badge>}
          <span className="text-micro text-ink-400 tnum">{cand.ref}</span>
        </div>
      </PageHeader>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="min-w-0 space-y-4 lg:col-span-2">
          <CandidateSections cand={cand} ocr={ocr} />

          <Panel flush>
            <PanelHeader title="Applications" meta="A candidate works one order at a time." />
            {apps.length === 0 ? (
              <EmptyState
                title="Not mapped to any order"
                description="The stage modal unlocks once this candidate is matched from an order page."
                action={!deployed ? <Button variant="primary" onClick={() => setMapping(true)}>Map to an order</Button> : undefined}
              />
            ) : (
              <ul>
                {apps.map((a) => {
                  const order = store.order(a.orderId)
                  return (
                    <li key={a.id}>
                      <button
                        onClick={() => setOpenApp(a.id)}
                        className="flex w-full items-center gap-3 border-b border-rule-soft px-4 py-3 text-left transition-colors last:border-0 hover:bg-field/70"
                      >
                        <StageChip
                          no={a.currentStage}
                          state={a.status === 'closed' ? 'closed' : a.status === 'deployed' ? 'done' : 'current'}
                        />
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-medium text-ink">
                            {store.orderLabel(a.orderId)}
                          </p>
                          <p className="truncate text-micro text-ink-400">
                            {order?.ref} · {a.status === 'active' ? stageByNo(a.currentStage).title : ''}
                            {a.closureReason ? closureLabels[a.closureReason] : ''}
                          </p>
                        </div>
                        <span className="shrink-0 text-micro text-ink-400">
                          {dateTimeLabel(a.updatedAt)}
                        </span>
                        <StatusPill status={a.status} />
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}
          </Panel>
        </div>

        <div className="space-y-4">
          <Panel flush>
            <PanelHeader
              title="Initial call"
              action={
                canWork && !active && !deployed && (
                  <Button size="sm" variant="secondary" icon={<PhoneCall className="h-3.5 w-3.5" />} onClick={() => setCalling(true)}>
                    {cand.callOutcome ? 'Update' : 'Record'}
                  </Button>
                )
              }
            />
            <div className="px-4 py-3.5">
              {(active || deployed) && <p className="mb-2 text-xs text-ink-500">Record further call outcomes in the application workflow.</p>}
              {cand.callOutcome ? (
                <>
                  <div className="flex items-center gap-2">
                    <Badge tone={cand.callOutcome.interested === 'yes' ? 'navy' : cand.callOutcome.interested === 'no' ? 'amber' : 'neutral'}>
                      {cand.callOutcome.interested === 'yes'
                        ? 'Interested'
                        : cand.callOutcome.interested === 'no'
                          ? 'Not interested'
                          : 'Not reachable'}
                    </Badge>
                    <span className="text-micro text-ink-400">
                      {smartTime(cand.callOutcome.recordedAt)}
                    </span>
                  </div>
                  <p className="mt-2 text-sm leading-snug text-ink-700">{cand.callOutcome.remarks}</p>
                </>
              ) : (
                <p className="text-sm text-ink-400">
                  No call recorded yet. The owner confirms interest before anything else moves.
                </p>
              )}
            </div>
          </Panel>

          <Panel flush>
            <PanelHeader title="Owner" />
            <div className="flex items-center gap-2.5 px-4 py-3.5">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-ink">{owner?.name || 'Needs assignment'}</p>
                <p className="truncate text-micro capitalize text-ink-400">{owner?.role}</p>
              </div>
              <CandidateAssignmentHistory candidateId={cand.id} />
            </div>
          </Panel>

          <Panel flush>
            <PanelHeader title="Documents" meta="View or download any version. Replacing a file keeps its history." />
            {canWork && <p className="px-4 pb-2 text-micro text-ink-400">{DOCUMENT_HELP}</p>}
            <div className="px-4">
              {cand.documents.map((d) => (
                <DocumentSlotRow
                  key={d.key}
                  label={d.label}
                  versions={d.versions}
                  uploaderName={(userId) => store.user(userId)?.name || 'Former team member'}
                  onUpload={
                    canWork && d.key !== 'source_bundle'
                      ? async (file) => {
                          await api.upload('candidate', cand.id, d.key, file)
                          try {
                            await store.refresh()
                            store.notify(`${d.label} uploaded`)
                          } catch (err) {
                            store.notify(`${d.label} was uploaded. Reload the page to see the saved version.`, 'warn')
                          }
                        }
                      : undefined
                  }
                />
              ))}
              {cand.documents.some((d) => d.key === 'source_bundle' && d.versions.length > 0) && (
                <p className="py-2 text-micro text-ink-400">Original documents are kept as received. Upload corrections to the matching document above.</p>
              )}
              <EmailEvidenceHistory candidateId={cand.id} />
            </div>
          </Panel>
        </div>
      </div>

      <CallDialog open={calling} onClose={() => setCalling(false)} candidateId={cand.id} />
      {editing && <CandidateEditDialog candidate={cand} onClose={() => setEditing(false)} />}
      {mapping && <MapToOrderDialog open onClose={() => setMapping(false)} candidateId={cand.id} />}
      <StageModal applicationId={openApp} open={!!openApp} onClose={() => setOpenApp(null)} />
    </>
  )
}

function CallDialog({
  open,
  onClose,
  candidateId,
}: {
  open: boolean
  onClose: () => void
  candidateId: string
}) {
  const { recordCall } = useStore()
  const [interested, setInterested] = useState('')
  const [remarks, setRemarks] = useState('')
  const [called, setCalled] = useState('yes')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  return (
    <Dialog
      open={open}
      onClose={() => { if (!saving) onClose() }}
      title="Record the call outcome"
      description="A confirmed interested or not-interested outcome completes the initial-call task. An unanswered or unmade call remains open."
      footer={
        <>
          <Button variant="ghost" disabled={saving} onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={saving || !interested}
            onClick={async () => {
              if (saving) return
              setSaving(true); setError('')
              try {
                await recordCall(candidateId, interested, remarks, called === 'yes')
                setInterested(''); setRemarks(''); setCalled('yes'); onClose()
              } catch (error) { setError(error instanceof Error ? error.message : 'The call outcome was not saved.') } finally { setSaving(false) }
            }}
          >
            {saving ? 'Saving…' : 'Save outcome'}
          </Button>
        </>
      }
    >
      <fieldset disabled={saving} className="min-w-0 space-y-4">
        <Field label="Called" required><RadioRow name="called" value={called} onChange={value => { setCalled(value); if (value === 'no') setInterested('unreachable') }} options={[{ value: 'yes', label: 'Yes' }, { value: 'no', label: 'No' }]} /></Field>
        <Field label="Interested" required>
          <RadioRow
            name="interested"
            value={interested}
            onChange={setInterested}
            options={called === 'no' ? [{ value: 'unreachable', label: 'Not contacted yet' }] : [
              { value: 'yes', label: 'Yes' },
              { value: 'no', label: 'No' },
              { value: 'unreachable', label: 'Not reachable' },
            ]}
          />
        </Field>
        {error && <p role="alert" className="text-sm text-stamp-600">{error}</p>}
        <Field label="Remarks">
          <Textarea
            maxLength={10000}
            value={remarks}
            onChange={(e) => setRemarks(e.target.value)}
            placeholder="What the candidate said"
          />
        </Field>
      </fieldset>
    </Dialog>
  )
}
