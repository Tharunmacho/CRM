import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, PhoneCall, Link2 } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { dateTimeLabel, smartTime } from '@/shared/lib/format'
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

export default function CandidateProfilePage() {
  const { id } = useParams()
  const store = useStore()
  const navigate = useNavigate()
  const cand = store.candidates.find((c) => c.id === id)

  const [openApp, setOpenApp] = useState<string | null>(null)
  const [calling, setCalling] = useState(false)
  const [mapping, setMapping] = useState(false)

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
            {!active && (
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
                action={<Button variant="primary" onClick={() => setMapping(true)}>Map to an order</Button>}
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
                canWork && (
                  <Button size="sm" variant="secondary" icon={<PhoneCall className="h-3.5 w-3.5" />} onClick={() => setCalling(true)}>
                    {cand.callOutcome ? 'Update' : 'Record'}
                  </Button>
                )
              }
            />
            <div className="px-4 py-3.5">
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
                <p className="truncate text-sm font-medium text-ink">{owner?.name}</p>
                <p className="truncate text-micro capitalize text-ink-400">{owner?.role}</p>
              </div>
            </div>
          </Panel>

          <Panel flush>
            <PanelHeader title="Documents" meta="Nothing is overwritten — replacing a file adds a version." />
            <div className="px-4">
              {cand.documents.map((d) => (
                <DocumentSlotRow
                  key={d.key}
                  label={d.label}
                  versions={d.versions}
                  onUpload={
                    canWork
                      ? async (file) => {
                          try {
                            await api.upload('candidate', cand.id, d.key, file)
                            await store.refresh()
                            store.notify(`${d.label} uploaded`)
                          } catch (err) {
                            store.notify((err as Error).message || 'Upload failed', 'warn')
                          }
                        }
                      : undefined
                  }
                />
              ))}
            </div>
          </Panel>
        </div>
      </div>

      <CallDialog open={calling} onClose={() => setCalling(false)} candidateId={cand.id} />
      <MapToOrderDialog open={mapping} onClose={() => setMapping(false)} candidateId={cand.id} />
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

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Record the call outcome"
      description="This closes the initial-call task. Not interested keeps the candidate in the Data Bank for other orders."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!interested}
            onClick={() => {
              void recordCall(candidateId, interested, remarks)
              setInterested('')
              setRemarks('')
              onClose()
            }}
          >
            Save outcome
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Interested" required>
          <RadioRow
            name="interested"
            value={interested}
            onChange={setInterested}
            options={[
              { value: 'yes', label: 'Yes' },
              { value: 'no', label: 'No' },
              { value: 'unreachable', label: 'Not reachable' },
            ]}
          />
        </Field>
        <Field label="Remarks">
          <Textarea
            value={remarks}
            onChange={(e) => setRemarks(e.target.value)}
            placeholder="What the candidate said"
          />
        </Field>
      </div>
    </Dialog>
  )
}
