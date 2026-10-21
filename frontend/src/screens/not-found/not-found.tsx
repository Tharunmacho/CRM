import { EmptyState } from '@/shared/ui/display'

export default function NotFoundPage() {
  return (
    <EmptyState
      title="That page does not exist"
      description="Check the address, or pick a section from the sidebar."
    />
  )
}
