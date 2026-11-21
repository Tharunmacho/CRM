import type { ReactNode } from 'react'
import { PageHeader } from '@/shared/ui/display'

/** The header every tab of the Reports page shares: the title and the tab's own buttons. */
export function ReportsHeader({ actions }: { tab?: string; actions?: ReactNode }) {
  return <PageHeader title="Reports" actions={actions} />
}
