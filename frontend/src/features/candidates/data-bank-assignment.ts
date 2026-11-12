import type { Application } from '@/shared/model/types'

/** The Data Bank shows the active placement, otherwise the latest deployed one. */
export function bankApplication(applications: readonly Application[]): Application | undefined {
  return applications
    .filter(application => application.status === 'active' || application.status === 'deployed')
    .sort((left, right) => {
      const statusOrder = Number(left.status !== 'active') - Number(right.status !== 'active')
      return statusOrder || latestFirst(left, right)
    })[0]
}

/** An older closed application remains accessible from the form when no placement is shown. */
export function bankFormApplication(applications: readonly Application[]): Application | undefined {
  return bankApplication(applications) ?? [...applications].sort(latestFirst)[0]
}

function latestFirst(left: Application, right: Application): number {
  const createdOrder = Date.parse(right.createdAt) - Date.parse(left.createdAt)
  return createdOrder || (left.id < right.id ? 1 : left.id > right.id ? -1 : 0)
}
