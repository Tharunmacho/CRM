/** Merge a duplicate candidate into the existing one (admin only). The rules and the work are the server's; see duplicate-merge.ts. */
import { api } from '@/shared/api/client'
import { parseMergePlan, type MergePlan } from './duplicate-merge'

const path = (sourceId: string, targetId: string) => `/candidates/${encodeURIComponent(sourceId)}/merge-into/${encodeURIComponent(targetId)}`

/** What the merge would move, skip or refuse. Changes nothing. */
export async function previewMerge(sourceId: string, targetId: string, confirmIdentity = false): Promise<MergePlan> {
  return parseMergePlan(await api.post<unknown>(path(sourceId, targetId), { dryRun: true, confirmIdentity }))
}

/** Moves everything worth keeping onto `targetId` and removes `sourceId`. A refusal is an ApiError (409) with the reason in plain words. */
export async function mergeCandidate(sourceId: string, targetId: string, confirmIdentity = false): Promise<MergePlan> {
  return parseMergePlan(await api.post<unknown>(path(sourceId, targetId), { confirmIdentity }))
}
