import { ClipboardList } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@/shared/ui/primitives'
import { applicationFormHref } from '../form-model'

/** The entry point to a candidate's application form, from any list or card. Always pass the order when it is known. */
export function ApplicationFormButton({
  candidateId,
  orderId,
  size = 'md',
  variant = 'secondary',
  iconOnly,
  className,
  label = 'Application form',
}: {
  candidateId: string
  orderId?: string | null
  size?: 'sm' | 'md'
  variant?: 'primary' | 'secondary' | 'ghost'
  iconOnly?: boolean
  className?: string
  label?: string
}) {
  const navigate = useNavigate()
  return (
    <Button
      size={size}
      variant={variant}
      className={iconOnly ? `${className ?? ''} !px-0 w-8`.trim() : className}
      icon={<ClipboardList />}
      title={`Open the application form${orderId ? ' for this order' : ''}`}
      aria-label={iconOnly ? label : undefined}
      onClick={(event) => { event.stopPropagation(); navigate(applicationFormHref(candidateId, orderId)) }}
    >
      {iconOnly ? null : label}
    </Button>
  )
}
