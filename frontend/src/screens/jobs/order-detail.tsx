import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, History, Link2, Pencil, Trash2, Unlink } from 'lucide-react'
import { dateLabel, dateTimeLabel, money } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { closureLabels, stageByNo } from '@/shared/workflow/stages'
import type { Application } from '@/shared/model/types'
import {
  Badge,
  Def,
  DefList,
  EmptyState,
  PageHeader,
  Panel,
  PanelHeader,
  StageDot,
  StatusPill,
} from '@/shared/ui/display'
import { Button } from '@/shared/ui/primitives'
import { Dialog } from '@/shared/ui/overlay'
import { DataTable, type Column } from '@/shared/ui/table'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { DeleteRecordDialog } from '@/shared/components/delete-dialog'
import { CandidateMatchDrawer } from '@/features/jobs/components/candidate-match-drawer'
import { OrderDialog } from '@/features/jobs/components/job-dialogs'
import { RecordAudit } from '@/features/master-data/components/record-audit'
import { useWorkspaceReady } from '@/features/master-data/use-workspace-ready'

export default function OrderDetailPage() {
  const { id } = useParams()
  const store = useStore()
  const snapshot = useWorkspaceReady(id ? `order:${id}` : null)
  const navigate = useNavigate()
  const order = store.orders.find((o) => o.id === id)

  const [matching, setMatching] = useState(false)
  const [openApp, setOpenApp] = useState<string | null>(null)
  const [unmapping, setUnmapping] = useState<string | null>(null)
  const [unmapSaving, setUnmapSaving] = useState(false)
  const [unmapError, setUnmapError] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [editing, setEditing] = useState(false)
  const [history, setHistory] = useState(false)

  if (!snapshot.ready) return <Panel><p role={snapshot.error ? 'alert' : 'status'} className="text-sm text-ink-600">{snapshot.error || 'Loading the latest hiring order and applications…'}</p>{snapshot.error && <Button className="mt-3" onClick={snapshot.retry}>Retry order</Button>}</Panel>
  if (!order) {
    return (
      <EmptyState
        title="That order does not exist"
        description="It may have been closed and removed."
        action={<Button onClick={() => navigate('/jobs')}>Back to Jobs</Button>}
      />
    )
  }

  const client = store.company(order.companyId)
  const designation = store.designation(order.designationId)
  if (!client || !designation) return <EmptyState title="This order's employer or designation is unavailable" description="Refresh to check the latest hiring records." action={<Button onClick={snapshot.retry}>Refresh order</Button>} />
  const canMatch = order.status === 'open'
  const apps = store.applications.filter((a) => a.orderId === order.id)
  const activeApps = apps.filter((a) => a.status === 'active')
  const deployed = apps.filter((a) => a.status === 'deployed')
  const selected = activeApps.filter((a) => a.currentStage > 4)

  const matchedColumns: Column<Application>[] = [
    {
      key: 'candidate',
      header: 'Candidate',
      sortable: true,
      width: '26%',
      value: (a) => store.candidate(a.candidateId)?.name ?? '',
      render: (a) => {
        const c = store.candidate(a.candidateId)
        return (
          <div className="min-w-0">
            <p className="truncate font-semibold text-ink">{c?.name ?? 'Candidate unavailable'}</p>
            <p className="truncate text-micro text-ink-400 ident">{c?.phone ?? a.candidateId}</p>
          </div>
        )
      },
    },
    {
      key: 'stage',
      header: 'Current stage',
      sortable: true,
      filterable: true,
      value: (a) =>
        a.status === 'closed' && a.closureReason
          ? closureLabels[a.closureReason]
          : `${a.currentStage}. ${stageByNo(a.currentStage).title}`,
      render: (a) =>
        a.status === 'closed' && a.closureReason ? (
          <span className="text-stamp-700">{closureLabels[a.closureReason]}</span>
        ) : (
          <span className="flex items-center gap-2 whitespace-nowrap">
            <StageDot no={a.currentStage} />
            <span className="text-ink-700">{stageByNo(a.currentStage).title}</span>
          </span>
        ),
    },
    {
      key: 'owner',
      header: 'Owner',
      sortable: true,
      filterable: true,
      className: 'whitespace-nowrap',
      value: (a) => store.user(store.candidate(a.candidateId)?.ownerId ?? '')?.name ?? '',
    },
    {
      key: 'updated',
      header: 'Last update',
      sortable: true,
      className: 'whitespace-nowrap',
      value: (a) => a.updatedAt,
      render: (a) => <span className="text-ink-500">{dateTimeLabel(a.updatedAt)}</span>,
    },
    {
      key: 'status',
      header: 'Status',
      filterable: true,
      value: (a) => a.status,
      render: (a) => <StatusPill status={a.status} />,
    },
    {
      key: 'unmap',
      header: '',
      align: 'right',
      render: (a) =>
        a.status === 'active' && store.can('map_candidates') ? (
          <button
            onClick={(e) => {
              e.stopPropagation()
              setUnmapping(a.id)
              setUnmapError('')
            }}
            aria-label={`Unmap ${store.candidate(a.candidateId)?.name ?? 'candidate'}`}
            title="Unmap"
            className="rounded-control p-1.5 text-ink-400 hover:bg-stamp-50 hover:text-stamp-600"
          >
            <Unlink className="h-3.5 w-3.5" />
          </button>
        ) : null,
    },
  ]

  return (
    <>
      <Link to={`/jobs?company=${encodeURIComponent(order.companyId)}`} className="mb-3 inline-flex items-center gap-1.5 text-xs text-ink-500 hover:text-ink">
        <ArrowLeft className="h-3.5 w-3.5" />
        Jobs / Positions
      </Link>

      <PageHeader
        title={designation.title}
        description={[client.name, [order.city, order.country].filter(Boolean).join(', '), order.ref].filter(Boolean).join(' · ')}
        actions={
          <>
            <Button icon={<History className="h-4 w-4" />} onClick={() => setHistory(true)}>Activity</Button>
            <Button icon={<Pencil className="h-4 w-4" />} onClick={() => setEditing(true)}>Edit order</Button>
            {store.can('delete_records') && (
              <Button variant="dangerGhost" icon={<Trash2 className="h-4 w-4" />} onClick={() => setDeleting(true)}>
                Delete order
              </Button>
            )}
            <Button variant="primary" icon={<Link2 className="h-4 w-4" />} disabled={!canMatch} onClick={() => setMatching(true)}>
              Match candidates
            </Button>
          </>
        }
      />

      <Panel className="mb-4">
        <div className="flex flex-wrap items-start justify-between gap-4"><div><p className="workspace-eyebrow">Candidate requirements</p><h2 className="mt-1 text-lg font-semibold">{designation.title} · {designation.minExperience} years minimum experience</h2><p className="mt-2 max-w-[75ch] text-sm text-ink-500">{designation.description || 'Review saved skills and experience before starting prescreening. Missing profile details can be verified with the candidate.'}</p></div><Button onClick={() => navigate(`/clients/${client.clientId}`)}>View client relationship</Button></div>
        <div className="mt-4 flex flex-wrap gap-2">{designation.requiredSkills.length ? designation.requiredSkills.map(skill => <Badge key={skill} tone="navy">{skill}</Badge>) : <span className="text-sm text-ink-500">No required skills specified</span>}</div>
        {!canMatch && <p role="status" className="mt-4 border-t border-rule-soft pt-4 text-sm text-amber-700">New matching is paused because this order is not open. Existing applications and their history remain available.</p>}
      </Panel>

      <div className="grid gap-4 lg:grid-cols-4">
        <Panel className="min-w-0 lg:col-span-3">
          <DefList cols={3}>
            <Def label="Status">
              <StatusPill status={order.status} />
            </Def>
            <Def label="Openings">
              <span className="tnum">{order.openings}</span>
            </Def>
            <Def label="Salary">{money(order.salary, order.currency)}</Def>
            <Def label="Contract period">{order.contractPeriod}</Def>
            <Def label="Interview date">{dateLabel(order.interviewDate)}</Def>
            <Def label="Order deadline">{dateLabel(order.deadline)}</Def>
          </DefList>
          <div className="mt-3.5 flex flex-wrap gap-1.5">
            {order.benefits.map((b) => (
              <Badge key={b}>{b}</Badge>
            ))}
          </div>
        </Panel>

        <Panel>
          <p className="text-xs text-ink-500">Progress</p>
          <p className="mt-2 text-stat font-semibold text-ink tnum">
            {deployed.length}
            <span className="text-lead font-normal text-ink-400">/{order.openings}</span>
          </p>
          <p className="text-micro text-ink-400">deployed</p>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-field">
            <div
              className="h-full rounded-full bg-navy-600"
              style={{ width: `${Math.min(100, (deployed.length / order.openings) * 100)}%` }}
            />
          </div>
          <dl className="mt-3 space-y-1.5 border-t border-rule-soft pt-3 text-xs">
            <div className="flex justify-between">
              <dt className="text-ink-500">Matched</dt>
              <dd className="tnum font-medium text-ink">{activeApps.length}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-ink-500">Selected</dt>
              <dd className="tnum font-medium text-ink">{selected.length}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-ink-500">Closed</dt>
              <dd className="tnum font-medium text-ink">{apps.filter((a) => a.status === 'closed').length}</dd>
            </div>
          </dl>
        </Panel>
      </div>

      <Panel flush className="mt-4">
        <PanelHeader title="Matched candidates" meta={`${apps.length} applications on this order`} />
        {apps.length === 0 ? (
          <EmptyState
            title="No candidates matched yet"
            description="Search the Data Bank and match candidates. Matching is what unlocks their stage modal."
            action={<Button variant="primary" disabled={!canMatch} onClick={() => setMatching(true)}>Match candidates</Button>}
          />
        ) : (
          <DataTable
            rows={apps}
            columns={matchedColumns}
            rowKey={(a) => a.id}
            onRowClick={(a) => setOpenApp(a.id)}
            pageSize={15}
          />
        )}
      </Panel>

      {matching && <CandidateMatchDrawer key={order.id} onClose={() => setMatching(false)} orderId={order.id} />}

      <Dialog
        open={!!unmapping}
        onClose={() => { if (!unmapSaving) setUnmapping(null) }}
        title="Unmap this candidate?"
        description="The application closes with reason Unmapped and its open tasks are cancelled. The candidate returns to the Data Bank and can be mapped to another order."
        footer={
          <>
            <Button variant="ghost" disabled={unmapSaving} onClick={() => setUnmapping(null)}>
              Keep the mapping
            </Button>
            <Button
              variant="danger"
              disabled={unmapSaving}
              onClick={async () => {
                if (!unmapping || unmapSaving) return
                setUnmapSaving(true); setUnmapError('')
                try { await store.unmap(unmapping); setUnmapping(null) }
                catch (error) { setUnmapError(error instanceof Error ? error.message : 'The candidate could not be unmapped. Please try again.') }
                finally { setUnmapSaving(false) }
              }}
            >
              {unmapSaving ? 'Unmapping…' : 'Unmap'}
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink-600">
          Stages already completed stay on the closed application as a record. Remapping starts a
          fresh application at stage 1.
        </p>
        {unmapError && <p role="alert" className="mt-3 text-sm text-stamp-700">{unmapError}</p>}
      </Dialog>

      <StageModal applicationId={openApp} open={!!openApp} onClose={() => setOpenApp(null)} />
      {editing && <OrderDialog order={order} onClose={() => setEditing(false)} />}
      {history && <RecordAudit kind="order" id={order.id} onClose={() => setHistory(false)} />}
      <DeleteRecordDialog
        kind="order"
        id={order.id}
        open={deleting}
        onClose={() => setDeleting(false)}
        onDeleted={() => navigate('/jobs')}
        onEdit={() => setEditing(true)}
      />
    </>
  )
}
