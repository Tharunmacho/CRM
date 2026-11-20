import { downloadApiFile } from '@/shared/api/download'
import { exportFileName, exportPath, type DailyView } from './daily-work-model'

/** Download the Tasks list as an Excel file: `staff` is a recruiter's id or "all" (the same filter as the list), `person` names the file, `view` is the status view on screen. */
export function downloadDailyWork(staff: string | undefined, day: string, person?: string, view: DailyView = 'current') {
  return downloadApiFile(exportPath(staff, view), exportFileName(day, person, view))
}
