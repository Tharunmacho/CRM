import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'

export interface SkillCoverage {
  skills: { key: string; label: string; count: number }[]
  total: number
  recorded: number
  missing: number
}

/** Facets come from the entire server pool, never from one page or old bootstrap. */
export function useSkillFacets(params: string, revision: unknown, enabled = true) {
  const [snapshot, setSnapshot] = useState<{ key: string; data: SkillCoverage } | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    setLoading(true); setError('')
    void api.get<SkillCoverage>(`/candidates/skill-facets?${params}`, { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) setSnapshot({ key: params, data }) })
      .catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Skill groups could not load.') })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [params, revision, enabled, retry])
  return { data: !error && snapshot?.key === params ? snapshot.data : null, error, loading, refresh: () => setRetry(value => value + 1) }
}
