/**
 * Interest level of a candidate after the first call: four choices, each with its own colour.
 * It maps onto the existing initial-call outcome (`callOutcome.interested`): yes / no.
 */
export type InterestLevel = 'more_interested' | 'interested' | 'somewhat_interested' | 'not_interested'

export const INTEREST_LEVELS: { value: InterestLevel; label: string; hint: string; tone: 'green' | 'blue' | 'amber' | 'red' }[] = [
  { value: 'more_interested', label: 'More interested', hint: 'Ready to move quickly', tone: 'green' },
  { value: 'interested', label: 'Interested', hint: 'Wants to hear the details', tone: 'blue' },
  { value: 'somewhat_interested', label: 'Somewhat interested', hint: 'Needs convincing', tone: 'amber' },
  { value: 'not_interested', label: 'Not interested', hint: 'Does not want to proceed', tone: 'red' },
]

/** The call result the CRM already stores for an interest level. */
export function callInterestFor(level: InterestLevel | ''): 'yes' | 'no' | '' {
  if (!level) return ''
  return level === 'not_interested' ? 'no' : 'yes'
}
