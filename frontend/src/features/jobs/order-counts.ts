import type { Application } from '@/shared/model/types'

/**
 * The one definition of the order numbers every screen shows (dashboard, reports, client page,
 * order page, order cards). The PRD names "matched, selected, deployed" but does not define them,
 * so the lead decided:
 *  - matched  = applications that are still active on the order (not closed, not yet deployed)
 *  - selected = active applications past stage 4 "Interview result", i.e. the client picked them
 *  - deployed = applications that finished stage 11
 * Closed applications (unmapped, rejected, not interested, ...) never count. The backend keeps the
 * same rule in app/services/reporting.py (`order_counts`).
 */
export const SELECTED_PAST_STAGE = 4

type Counted = Pick<Application, 'status' | 'currentStage'>

export const isMatched = (application: Counted) => application.status === 'active'
export const isSelected = (application: Counted) => application.status === 'active' && application.currentStage > SELECTED_PAST_STAGE
export const isDeployed = (application: Counted) => application.status === 'deployed'

export interface OrderCounts { matched: number; selected: number; deployed: number }

export function orderCounts(applications: Counted[]): OrderCounts {
  let matched = 0
  let selected = 0
  let deployed = 0
  for (const application of applications) {
    if (isMatched(application)) matched += 1
    if (isSelected(application)) selected += 1
    if (isDeployed(application)) deployed += 1
  }
  return { matched, selected, deployed }
}
