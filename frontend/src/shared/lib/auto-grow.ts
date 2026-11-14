/**
 * Pure maths behind the auto-growing textarea (see shared/ui/use-auto-grow.ts). No DOM here so it can be unit tested.
 *
 * The caller measures `scrollHeight` with the height cleared, then asks how tall the box should be: as tall as the text
 * needs, never shorter than `minRows` lines and never taller than `maxRows` lines (after that the box scrolls inside).
 */

export interface GrowInput {
  /** Textarea.scrollHeight measured with `height: auto` - content plus padding, excluding borders. */
  scrollHeight: number
  /** Computed line height in px. */
  lineHeight: number
  /** Top + bottom padding in px. */
  paddingY: number
  /** Top + bottom border width in px. */
  borderY: number
  /** True for `box-sizing: border-box` (the default in this app). */
  borderBox: boolean
  minRows: number
  /** Omit, 0 or Infinity for "never clamp". */
  maxRows?: number
}

export interface GrowResult {
  /** Value for `style.height`, in px, already correct for the box-sizing. */
  height: number
  /** True when the text is taller than the cap, so the box must scroll inside. */
  scrolls: boolean
}

const finite = (value: number, fallback: number) => (Number.isFinite(value) ? value : fallback)

export function clampRows(value: number | undefined, fallback: number): number {
  if (value === undefined || !Number.isFinite(value)) return fallback
  return Math.max(1, Math.floor(value))
}

export function growHeight(input: GrowInput): GrowResult {
  const lineHeight = Math.max(1, finite(input.lineHeight, 20))
  const paddingY = Math.max(0, finite(input.paddingY, 0))
  const borderY = Math.max(0, finite(input.borderY, 0))
  const scrollHeight = Math.max(0, finite(input.scrollHeight, 0))
  const minRows = clampRows(input.minRows, 1)
  const capped = input.maxRows !== undefined && Number.isFinite(input.maxRows) && input.maxRows > 0
  const maxRows = capped ? Math.max(minRows, clampRows(input.maxRows, minRows)) : Infinity

  const chrome = paddingY + borderY
  const wanted = Math.ceil(scrollHeight + borderY)
  const minOuter = Math.ceil(minRows * lineHeight + chrome)
  const maxOuter = capped ? Math.ceil(maxRows * lineHeight + chrome) : Infinity
  const outer = Math.min(Math.max(wanted, minOuter), maxOuter)
  return {
    height: input.borderBox ? outer : Math.max(0, outer - chrome),
    scrolls: wanted > maxOuter,
  }
}
