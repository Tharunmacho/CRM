import { useNavigate, useSearchParams } from 'react-router-dom'
import { Tabs } from '@/shared/ui/overlay'
import { bucketTabs, type BucketKey } from '@/features/daily-work/daily-work-model'
import { useDailyWork, useFollowUp } from '@/features/daily-work/use-daily-work'

/** The counts in the tab labels: candidates still to work today, and candidates in follow-up. Both lists are already cached by the screens. */
export function useBucketCounts(staff?: string) {
  const today = useDailyWork({ staff, debounceMs: 4000 })
  const followUp = useFollowUp({ staff, debounceMs: 4000 })
  return { today: today.data?.summary.toWork ?? null, followUp: followUp.data?.summary.total ?? null }
}

/** Opens a bucket: Today's candidates is the plain /tasks, Follow-up is ?view=follow-up and Travelled is its own screen. */
export function useOpenBucket() {
  const navigate = useNavigate()
  const [, setParams] = useSearchParams()
  return (key: string) => {
    if (key === 'travelled') { navigate('/travelled'); return }
    setParams((previous) => {
      const next = new URLSearchParams(previous)
      for (const name of ['task', 'stage', 'date', 'from', 'to']) next.delete(name)
      if (key === 'daily') next.delete('view'); else next.set('view', key)
      return next
    })
  }
}

/** The strip at the top of Today's candidates and Follow-up (the 48-hour task list has the same three tabs in front of its own). */
export function TasksTabs({ active, staff }: { active: Exclude<BucketKey, 'travelled'>; staff?: string }) {
  const counts = useBucketCounts(staff)
  const open = useOpenBucket()
  return <Tabs className="dw-tabs" tabs={bucketTabs(counts)} active={active} onChange={open} />
}
