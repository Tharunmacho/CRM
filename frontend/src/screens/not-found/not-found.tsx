import { useNavigate } from 'react-router-dom'
import { EmptyState } from '@/shared/ui/display'
import { Button } from '@/shared/ui/primitives'

export default function NotFoundPage() {
  const navigate = useNavigate()
  return (
    <EmptyState
      title="We could not find this page"
      description="The address may be wrong, or the page may have moved. Pick a page from the menu, or go back to the dashboard."
      action={<Button variant="primary" onClick={() => navigate('/')}>Go to dashboard</Button>}
    />
  )
}
