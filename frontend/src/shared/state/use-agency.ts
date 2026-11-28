import { useEffect } from 'react'
import { agencyName, agencyShortName, documentTitle, homeDigits } from '@/shared/lib/agency'
import { useOptionalStore, useStore } from './crm-store'

/**
 * The agency's name from the database (or, before sign-in, from the server's anonymous answer). Also keeps the
 * browser tab title in step with it: the page ships with a neutral "CRM" title and this replaces it at run time.
 */
export function useAgency() {
  const { settings, publicAgency } = useStore()
  const name = agencyName(settings, publicAgency)
  const shortName = agencyShortName(settings, publicAgency)
  useEffect(() => { document.title = documentTitle(name).toLowerCase() }, [name])
  return { name, shortName }
}

/** The agency's home calling code as digits ("91"), or "" until the settings arrive: what the phone helpers add to a national number. */
export function useHomeDigits(): string {
  return homeDigits(useOptionalStore()?.settings.homeCallingCode)
}
