import type { KeyboardEvent } from 'react'
import { cn } from '@/shared/lib/cn'
import { dateLabel, money } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import type { Order } from '@/shared/model/types'
import { orderStatusKey } from '@/features/jobs/order-meta'
import { orderCounts } from '@/features/jobs/order-counts'
import { Avatar, Place } from '@/features/jobs/components/identity'
import { DueChip, OrderRowActions, OrderStatusText, type OrderActionHandlers } from '@/features/jobs/components/order-parts'

export { orderStatusKey }

/** One client's opening, with every PRD order field at a glance. Flat card: hairline border, no shadow. */
export function OrderCard({ order, handlers, showDesignation }: { order: Order; handlers: OrderActionHandlers; showDesignation?: boolean }) {
  const store = useStore()
  const company = store.company(order.companyId)
  const designation = store.designation(order.designationId)
  const apps = store.applications.filter((application) => application.orderId === order.id)
  const { matched, selected, deployed } = orderCounts(apps)
  const share = Math.min(100, order.openings ? (deployed / order.openings) * 100 : 0)
  const status = orderStatusKey(order)
  const name = company?.name ?? 'Unknown company'
  const open = () => handlers.onOpen(order)
  const onKey = (event: KeyboardEvent<HTMLElement>) => {
    if (event.target !== event.currentTarget) return
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open() }
  }

  return (
    <article className={cn('jv-order', `st-${status}`)} tabIndex={0} aria-label={`Order ${order.ref} for ${name}`} onClick={open} onKeyDown={onKey}>
      <header>
        <Avatar name={name} size="lg" />
        <div className="jv-order-title">
          <h4 title={showDesignation ? designation?.title ?? 'Unknown job title' : name}>{showDesignation ? designation?.title ?? 'Unknown job title' : name}</h4>
          <p>{showDesignation ? name : designation?.title ?? 'Unknown job title'} · <span className="ident">{order.ref}</span></p>
        </div>
        <OrderStatusText order={order} />
      </header>

      <p className="jv-order-place"><Place city={order.city} country={order.country} empty="Work place not set" /></p>

      <div className="jv-order-progress">
        <p><b className="tnum">{deployed}</b><span> of {order.openings} {order.openings === 1 ? 'opening' : 'openings'} deployed</span>{matched > 0 && <em>{matched} matched{selected > 0 ? ` · ${selected} selected` : ''}</em>}</p>
        <span className="jv-bar" role="img" aria-label={`${deployed} of ${order.openings} openings deployed`}><span style={{ width: `${share}%` }} /></span>
      </div>

      <dl className="jv-order-facts">
        <div><dt>Salary</dt><dd>{order.salary ? `${money(order.salary, order.currency)} / month` : <i>Not set</i>}</dd></div>
        <div><dt>Contract</dt><dd>{order.contractPeriod || <i>Not set</i>}</dd></div>
        <div><dt>Interview</dt><dd>{order.interviewDate ? dateLabel(order.interviewDate) : <i>Not set</i>}</dd></div>
        <div><dt>Benefits</dt><dd title={order.benefits.join(', ')}>{order.benefits.length ? order.benefits.slice(0, 2).join(', ') + (order.benefits.length > 2 ? ` +${order.benefits.length - 2}` : '') : <i>None</i>}</dd></div>
      </dl>

      <footer>
        <DueChip order={order} short />
        <OrderRowActions order={order} handlers={handlers} name={`order ${order.ref}`} />
      </footer>
    </article>
  )
}
