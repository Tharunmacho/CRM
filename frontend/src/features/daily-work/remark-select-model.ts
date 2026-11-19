import { isAdminLevel } from '@/shared/model/role-labels'
/**
 * The REMARKS dropdown of the Tasks list, as plain data: which choices it offers, which one is selected, and when the current remark
 * is free text that is not a template ("custom": it stays valid and shown as the select's value, and it is NEVER labelled as custom on
 * screen). The only way to type is the "Others" entry: it turns the cell into an inline box (OTHERS_MAX characters) that saves through the
 * same remark save.
 * No imports, so it runs under node:test.
 */

export interface TemplateChoice { id: string; text: string }

/** The value of the entry that stands for the row's current remark when it is not one of the templates. */
export const CUSTOM_VALUE = '__custom'

/** "Others": the last template-like entry. Choosing it opens the inline text box; nothing is saved by choosing it. */
export const OTHERS_VALUE = '__others'
export const OTHERS_LABEL = 'Others'
export const OTHERS_MAX = 500
export const isOthersChoice = (value: string): boolean => value === OTHERS_VALUE
/** The value of the temporary entry that shows the typed remark while it is being saved (the row's own remark replaces it afterwards). */
export const TYPED_VALUE = '__typed'

/** What the "Others" box saves: the typed text with its runs of spaces collapsed, or null when it is empty (empty = cancel). */
export function othersText(typed: string): string | null {
  const text = typed.replace(/\s+/g, ' ').trim().slice(0, OTHERS_MAX)
  return text || null
}

/** The value of the last row of the dropdown, shown to admins only: it opens an inline box to write a new template. */
export const ADD_VALUE = '__add'
export const ADD_LABEL = '+ Add a template'
export const isAddChoice = (value: string): boolean => value === ADD_VALUE

/** Only an admin or MD keeps the template list, so only they are offered the add row (the same check as the admin card). */
export const canAddTemplate = (role: string | null | undefined): boolean => isAdminLevel(role)

export interface RemarkChoice { value: string; label: string }

export interface RemarkSelectView {
  /** The selected entry: a template id, CUSTOM_VALUE, or '' when the candidate has no remark yet. */
  value: string
  /** The current remark is free text no template matches (only said once the templates have loaded). */
  custom: boolean
  /** In order: the "Select remark" prompt (only while nothing is chosen), the custom remark, the templates, "Others", then (admins) "+ Add a template". */
  options: RemarkChoice[]
}

export const SELECT_PLACEHOLDER = 'Select remark'

/** Capitals and runs of spaces do not make a remark different from its template. */
const key = (text: string): string => text.replace(/\s+/g, ' ').trim().toLowerCase()

export function remarkSelectView(current: string | null | undefined, templates: readonly TemplateChoice[], loaded = true, canAdd = false): RemarkSelectView {
  const body = (current ?? '').replace(/\s+/g, ' ').trim()
  const match = body ? templates.find((item) => key(item.text) === key(body)) : undefined
  const listed: RemarkChoice[] = templates.map((item) => ({ value: item.id, label: item.text }))
  const tail: RemarkChoice[] = [{ value: OTHERS_VALUE, label: OTHERS_LABEL }, ...(canAdd ? [{ value: ADD_VALUE, label: ADD_LABEL }] : [])]
  const options: RemarkChoice[] = [...listed, ...tail]
  if (match) return { value: match.id, custom: false, options }
  if (!body) return { value: '', custom: false, options: [{ value: '', label: SELECT_PLACEHOLDER }, ...options] }
  // Not a template: it is the selected entry (so it is shown), listed first and marked custom, and it stays valid. While the templates
  // are still loading nothing is claimed: a template remark must not flash as custom.
  return { value: CUSTOM_VALUE, custom: loaded, options: [{ value: CUSTOM_VALUE, label: body }, ...options] }
}

/** The text to save for a chosen entry, or null when the choice is not a template (the prompt, "Others", or the row's own custom remark). */
export function textForChoice(value: string, templates: readonly TemplateChoice[]): string | null {
  return templates.find((item) => item.id === value)?.text ?? null
}

export type RemarkSaveState = 'idle' | 'saving' | 'saved' | 'error'

/**
 * The status of the cell: Saving, Saved or the error, otherwise the muted `lead` (Follow-up's "Last update 3 days ago"). It is never
 * "Custom remark": a saved remark is simply the select's value. The Tasks table shows it as a badge on the control, never as a line.
 */
export function remarkStatus(state: RemarkSaveState, error = '', lead = ''): { text: string; tone: 'busy' | 'ok' | 'error' | 'lead' | 'none' } {
  if (state === 'saving') return { text: 'Saving…', tone: 'busy' }
  if (state === 'error') return { text: error || 'The remark could not be saved.', tone: 'error' }
  if (state === 'saved') return { text: 'Saved', tone: 'ok' }
  if (lead) return { text: lead, tone: 'lead' }
  return { text: '', tone: 'none' }
}
