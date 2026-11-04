/**
 * Reusable building blocks of the application form. The Add candidate page uses the same parts so both screens look alike.
 *
 *   SectionCard / FieldGrid / GridField / EmptyRequired   section card + 4-column grid  (section-card.tsx)
 *   PhoneField, splitPhone, joinPhone, phoneHint          phone with country code        (phone-field.tsx, phone.ts)
 *   InterestCards, INTEREST_LEVELS, callInterestFor       four coloured interest cards   (interest-cards.tsx, interest.ts)
 *   RowTable                                              repeating-rows editor          (../components/sections.tsx)
 *   EntryRows                                             rows editor over JSON text     (../components/entry-rows.tsx)
 *   MissingChip, ProvenanceChip                           "Missing" / "Read from resume" (../components/fields.tsx)
 *   FormField, Cell                                       one docx field, view or edit   (../components/fields.tsx)
 * Styles: styles/application-form.css (class prefixes `af-` for the paper form, `afp-` for these parts).
 */
export { SectionCard, FieldGrid, GridField, EmptyRequired } from './section-card'
export { PhoneField } from './phone-field'
export { InterestCards } from './interest-cards'
export { COUNTRIES, DEFAULT_CODE, splitPhone, joinPhone, phoneHint, phoneDigitsOk, type Country } from './phone'
export { INTEREST_LEVELS, callInterestFor, type InterestLevel } from './interest'
export { RowTable } from '../components/sections'
export { EntryRows } from '../components/entry-rows'
export { MissingChip, ProvenanceChip, FormField, Cell } from '../components/fields'
