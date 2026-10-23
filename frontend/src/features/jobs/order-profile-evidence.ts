import type { Candidate, Designation } from '@/shared/model/types'
import { normalizedSkills, skillKey } from '@/features/candidates/candidate-skills'

/** These are exact saved-field comparisons, not a suitability score or eligibility gate. */
export function orderProfileEvidence(candidate: Pick<Candidate, 'designations' | 'skills' | 'totalExperience'>, designation: Designation) {
  const savedSkills = new Set(candidate.skills.map(skillKey))
  const required = normalizedSkills(designation.requiredSkills)
  return {
    designationMatches: candidate.designations.some(title => skillKey(title) === skillKey(designation.title)),
    matchedSkills: required.filter(skill => savedSkills.has(skillKey(skill))),
    missingSkills: required.filter(skill => !savedSkills.has(skillKey(skill))),
    requiredSkills: required,
    experienceMeets: candidate.totalExperience >= designation.minExperience,
  }
}
