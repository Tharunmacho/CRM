import type { Candidate } from '@/shared/model/types'
import { matchesSkills, skillKey } from '@/features/candidates/candidate-skills'

export interface CandidateMatchFilters {
  designation: string
  minExperience: string
  passport: 'any' | 'present' | 'missing'
  owner: string
  location: string
  skills: string[]
  skillMode: 'all' | 'any'
}

/** Filter recorded candidate facts; no inferred industries or skill aliases. */
export function matchesCandidate(
  candidate: Candidate,
  filters: CandidateMatchFilters,
  query: string,
): boolean {
  if (filters.designation && !candidate.designations.some((value) => skillKey(value) === skillKey(filters.designation))) return false
  if (filters.minExperience) {
    const minimum = Number(filters.minExperience)
    if (!Number.isFinite(minimum) || minimum < 0 || !Number.isFinite(candidate.totalExperience) || candidate.totalExperience < minimum) return false
  }
  const hasPassport = Boolean(candidate.passportNo?.trim())
  if (filters.passport === 'present' && !hasPassport) return false
  if (filters.passport === 'missing' && hasPassport) return false
  if (filters.owner !== 'all' && candidate.ownerId !== filters.owner) return false
  if (!skillKey(candidate.currentCity).includes(skillKey(filters.location))) return false
  if (!matchesSkills(candidate, filters.skills, filters.skillMode)) return false

  const haystack = [
    candidate.name, candidate.ref, candidate.phone, candidate.email ?? '',
    candidate.passportNo ?? '', candidate.currentCity, ...candidate.designations,
    ...candidate.skills, candidate.resumeText,
  ].join(' ')
  return skillKey(haystack).includes(skillKey(query))
}
