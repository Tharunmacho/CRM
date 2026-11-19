import { downloadApiFile } from '@/shared/api/download'
import { exportFileName, exportPath } from './daily-work-model'

/** Download the Tasks list as an Excel file: `staff` is a recruiter's id or "all" (the same filter as the list), `person` names the file. */
export function downloadDailyWork(staff: string | undefined, day: string, person?: string) {
  return downloadApiFile(exportPath(staff), exportFileName(day, person))
}
