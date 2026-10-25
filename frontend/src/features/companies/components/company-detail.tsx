import { Link } from 'react-router-dom'
import { BriefcaseBusiness, History, Pencil, Trash2 } from 'lucide-react'
import type { Company } from '@/shared/model/types'
import { useStore } from '@/shared/state/crm-store'
import { dateLabel } from '@/shared/lib/format'
import { Button } from '@/shared/ui/primitives'
import { Sheet } from '@/shared/ui/overlay'
import { StatusPill } from '@/shared/ui/display'
import { FileLink } from '@/shared/ui/file-drop'
import { DialogMark } from '@/features/master-data/components/dialog-mark'
import { orderCounts } from '@/features/jobs/order-counts'
import { AgreementPill, Avatar, Place, Progress } from '@/features/clients/components/parts'

const digits = (value: string) => value.replace(/[^\d]/g, '')
const empty = <span className="cd-none">Not added</span>

function Detail({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return <div className={className ? `cd-detail ${className}` : 'cd-detail'}><dt>{label}</dt><dd>{children}</dd></div>
}

/**
 * Company details in a side sheet: every field saved on one company (contact person, email, phone,
 * WhatsApp, address, agreement, notes), its client and its orders, with the actions that matter next.
 */
export function CompanyDetail({ company, onClose, onEdit, onHistory, onDelete, onOrders }: {
  company: Company
  onClose: () => void
  onEdit: () => void
  onHistory: () => void
  onDelete?: () => void
  onOrders: () => void
}) {
  const store = useStore()
  const client = store.client(company.clientId)
  const orders = store.orders.filter((order) => order.companyId === company.id).sort((a, b) => (a.status === 'open' ? 0 : 1) - (b.status === 'open' ? 0 : 1) || b.createdAt.localeCompare(a.createdAt))
  const open = orders.filter((order) => order.status === 'open')
  const openings = open.reduce((total, order) => total + order.openings, 0)
  const placed = open.reduce((total, order) => total + Math.min(orderCounts(store.applications.filter((application) => application.orderId === order.id)).deployed, order.openings), 0)

  return (
    <Sheet
      open
      onClose={onClose}
      title={company.name}
      description={client ? `Company of ${client.name}` : 'Company details'}
      footer={
        <>
          {onDelete && <Button variant="dangerGhost" icon={<Trash2 className="h-4 w-4" />} onClick={onDelete}>Delete</Button>}
          <Button variant="ghost" icon={<History className="h-4 w-4" />} onClick={onHistory}>History</Button>
          <Button icon={<Pencil className="h-4 w-4" />} onClick={onEdit}>Edit</Button>
          <Button variant="primary" icon={<BriefcaseBusiness className="h-4 w-4" />} onClick={onOrders}>View orders</Button>
        </>
      }
    >
      <DialogMark />
      <div className="cd-employer cd-sheet">
        <header>
          <Avatar name={company.name} size="lg" />
          <div className="cd-employer-title">
            <h3>{company.industry || 'Industry not added'}</h3>
            <p><Place country={company.country} city={company.city} /></p>
          </div>
          <div className="cd-employer-badges">
            <AgreementPill filed={company.agreement ? 1 : 0} total={1} short />
            <StatusPill status={company.status} />
          </div>
        </header>
        <dl className="cd-details">
          <Detail label="Client">{client ? <Link to={`/clients/${client.id}`}>{client.name}</Link> : empty}</Detail>
          <Detail label="Contact person">{company.contactPerson || empty}</Detail>
          <Detail label="Contact email">{company.contactEmail ? <a href={`mailto:${company.contactEmail}`}>{company.contactEmail}</a> : empty}</Detail>
          <Detail label="Phone">{company.phone ? <a href={`tel:${company.phone}`} className="ident">{company.phone}</a> : empty}</Detail>
          <Detail label="WhatsApp">{company.whatsapp ? <a href={`https://wa.me/${digits(company.whatsapp)}`} target="_blank" rel="noreferrer" className="ident">{company.whatsapp}</a> : empty}</Detail>
          <Detail label="Agreement" className="cd-span2">
            {company.agreement
              ? <FileLink url={company.agreement.url} className="cd-file">{company.agreement.name}</FileLink>
              : <span className="cd-none">No agreement uploaded yet. Press Edit to upload it.</span>}
          </Detail>
          <Detail label="Address" className="cd-wide">{company.address || empty}</Detail>
        </dl>
        {company.notes && <p className="cd-employer-notes">{company.notes}</p>}
      </div>

      <section className="cd-sheet-orders" aria-label="Orders">
        <h3>Orders</h3>
        {open.length > 0 && (
          <div className="cd-employer-stats">
            <span><b className="tnum">{open.length}</b> open {open.length === 1 ? 'order' : 'orders'} · <b className="tnum">{placed}</b> of <b className="tnum">{openings}</b> openings deployed</span>
            <Progress done={placed} total={openings} />
          </div>
        )}
        {orders.length === 0 ? <p className="cd-none">No orders for this company yet.</p> : (
          <ul className="cd-sheet-list">
            {orders.map((order) => (
              <li key={order.id}>
                <Link to={`/orders/${order.id}`}>
                  <span className="cd-sheet-main"><b>{store.designation(order.designationId)?.title ?? 'Order'}</b><small>{order.ref}{order.deadline ? ` · Closes ${dateLabel(order.deadline)}` : ''}</small></span>
                  <StatusPill status={order.status} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </Sheet>
  )
}
