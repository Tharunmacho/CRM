import { Clock3, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@/shared/ui/primitives'
import { useDayReminder } from './use-day-reminder'
import '@/styles/daily-report-reminder.css'

/**
 * A slim banner for a recruiter at the top of Tasks and the Dashboard: "Your daily report is not submitted yet" (after 17:00,
 * only while they have activity today). "Submit now" opens the Daily report with the My day card focused; the cross hides it for
 * the rest of the day.
 */
export function ReminderBanner() {
  const { show, dismiss } = useDayReminder()
  const navigate = useNavigate()
  if (!show) return null
  return <div className="dwr-reminder" role="status">
    <Clock3 size={16} aria-hidden="true" />
    <p><b>Your daily report is not submitted yet.</b></p>
    <Button size="sm" variant="primary" onClick={() => navigate('/reports?focus=myday')}>Submit now</Button>
    <button type="button" className="dwr-reminder-x" onClick={dismiss} aria-label="Hide this reminder for today"><X size={16} aria-hidden="true" /></button>
  </div>
}
