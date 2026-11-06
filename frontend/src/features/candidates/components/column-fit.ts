/**
 * Which columns of a fit-to-screen table are shown at a given box width. Pure (no React), so it is unit-tested.
 *
 * Every column has a minimum width taken from its content (a phone number in full, "04 Nov", a two-line pill). The table
 * gives each column at least that and shares what is left over in proportion. When the minimums do not add up to the box,
 * the least important optional columns (the highest `optional` number) are hidden first, so nothing is ever clipped.
 * `choices` is the person's own pick from the Columns menu: true keeps a column for as long as anything less wanted can
 * go instead, false hides it. Hidden columns are never lost: the row drawer and the cards still show every field.
 */
/** Room kept for the table's own vertical scrollbar. */
export const FIT_GUTTER = 16

export interface FitColumn { key: string; width: number; optional?: number; /** Hidden until the person switches it on (the custom question columns). */ off?: boolean }
export type ColumnChoices = Record<string, boolean>

export const fitsIn = (width: number, boxWidth: number) => width <= boxWidth - FIT_GUTTER

/** Spacing and type tighten to this share of the comfortable widths before any column is hidden. */
export const COMPACT_RATIO = 0.88
/** Special keys in the person's choices (besides one boolean per column). */
export const CHOICE_COMPACT = '__compact'
export const CHOICE_WIDE = '__wide'

export interface FitResult {
  /** Columns on screen, in table order. */
  keys: string[]
  /** The table is using its tighter spacing. */
  compact: boolean
  /** Columns the person wants but that did not fit. */
  hidden: number
  /** The person asked for the wide table: it may scroll sideways. */
  wide: boolean
}

export function fitColumns(columns: readonly FitColumn[], boxWidth: number, choices: ColumnChoices = {}): FitResult {
  const wanted = columns.filter((column) => (column.off ? choices[column.key] === true : choices[column.key] !== false))
  const wide = choices[CHOICE_WIDE] === true
  const forced = choices[CHOICE_COMPACT]
  if (wide || !(boxWidth > 0)) return { keys: wanted.map((column) => column.key), compact: forced === true, hidden: 0, wide }
  const shown = new Set(wanted.map((column) => column.key))
  const total = () => wanted.filter((column) => shown.has(column.key)).reduce((sum, column) => sum + column.width, 0)
  // Hide order: columns the person did not ask for first, then the ones they asked for; within each, the least important first.
  const candidates = () => wanted.filter((column) => shown.has(column.key) && column.optional)
    .sort((a, b) => Number(choices[a.key] === true) - Number(choices[b.key] === true) || (b.optional as number) - (a.optional as number))
  for (;;) {
    const width = total()
    if (forced !== true && fitsIn(width, boxWidth)) return { keys: wanted.filter((c) => shown.has(c.key)).map((c) => c.key), compact: false, hidden: wanted.length - shown.size, wide }
    if (forced !== false && fitsIn(width * COMPACT_RATIO, boxWidth)) return { keys: wanted.filter((c) => shown.has(c.key)).map((c) => c.key), compact: true, hidden: wanted.length - shown.size, wide }
    const next = candidates()[0]
    if (!next) return { keys: wanted.filter((c) => shown.has(c.key)).map((c) => c.key), compact: forced !== false, hidden: wanted.length - shown.size, wide }
    shown.delete(next.key)
  }
}

export const fitColumnKeys = (columns: readonly FitColumn[], boxWidth: number, choices: ColumnChoices = {}): string[] => fitColumns(columns, boxWidth, choices).keys

/** The columns that may be switched on or off, most important first. */
export const optionalColumns = <C extends FitColumn & { header: string }>(columns: readonly C[]) =>
  columns.filter((column) => column.optional && !column.off).sort((a, b) => (a.optional as number) - (b.optional as number))

const STORE = 'crm-db-columns'

export function readChoices(scope: string): ColumnChoices {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(`${STORE}:${scope}`) ?? '{}')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    return Object.fromEntries(Object.entries(parsed).filter(([, value]) => typeof value === 'boolean')) as ColumnChoices
  } catch { return {} }
}

export function saveChoices(scope: string, choices: ColumnChoices) {
  try {
    if (Object.keys(choices).length) window.localStorage.setItem(`${STORE}:${scope}`, JSON.stringify(choices))
    else window.localStorage.removeItem(`${STORE}:${scope}`)
  } catch { /* private mode: the choice lasts until the page closes */ }
}

export interface MenuColumn { key: string; header: string; group?: string; hint?: string }

/** Group, then the columns of that group, filtered by the menu's own search. */
export function groupMenuColumns(columns: MenuColumn[], groups: readonly string[], search: string) {
  const needle = search.trim().toLowerCase()
  const match = (column: MenuColumn) => !needle || column.header.toLowerCase().includes(needle) || (column.group ?? '').toLowerCase().includes(needle)
  const names = [...groups, ...new Set(columns.map((column) => column.group ?? 'Other').filter((name) => !groups.includes(name)))]
  return names.map((name) => ({ name, columns: columns.filter((column) => (column.group ?? 'Other') === name && match(column)) })).filter((group) => group.columns.length)
}

