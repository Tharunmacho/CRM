import { downloadApiFile } from '@/shared/api/download'
import { useReportData } from './report-api'
import { trackerFileName, trackerPath, type TrackerFilters, type TrackerResult } from './client-tracker-model'

/** The tracker rows for these filters, read like a report: the last answer shows at once while the server confirms it. */
export function useClientTracker(filters: TrackerFilters, sessionId: string) {
  return useReportData<TrackerResult>(trackerPath(filters), sessionId, '', ['reports', 'tasks'])
}

/** Download the same sheet as a file, with the filters of the screen. */
export function downloadClientTracker(filters: TrackerFilters, format: 'xlsx' | 'csv', day: string) {
  return downloadApiFile(trackerPath(filters, format), trackerFileName(day, format))
}
