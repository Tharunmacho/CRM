import { createContext, useContext } from 'react'

export const FieldLabelContext = createContext<string | undefined>(undefined)
export const CalendarContext = createContext<string | undefined>(undefined)

export function useFieldLabel() {
  return useContext(FieldLabelContext)
}
