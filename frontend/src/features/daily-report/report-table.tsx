import { Fragment, type ReactNode } from 'react'
import { cn } from '@/shared/lib/cn'
import { initials } from '@/shared/lib/format'
import { ScrollEdges } from '@/screens/tasks/daily-table'
import { useChangedCells } from './report-parts'
import { numberText, type Cells, type TableCol } from './daily-report-view'

export interface FigureRow {
  id: string
  name: string
  idle?: boolean
  cells: Cells
  /** Content of the last column. */
  tail?: ReactNode
  /** A row that is not a person to pick or drill into (the Unassigned row): plain name, plain numbers. */
  fixed?: boolean
}
export interface FigureGroup { key: string; title: string; icon: ReactNode; tone?: 'done'; rows: FigureRow[] }

/** The narrowest panel at which the figure tables are tables (the Tasks list's 1080px); below it every row is a card. */
export const TABLE_MIN_WIDTH = 1080
/** Side padding of a heading or figure cell in the wide table, both sides (8px + 8px), plus the 1px column line. */
const CELL_PAD = 17
let measureCanvas: CanvasRenderingContext2D | null | undefined
function canvas(): CanvasRenderingContext2D | null {
  if (measureCanvas === undefined) {
    try { measureCanvas = typeof document === 'undefined' ? null : document.createElement('canvas').getContext('2d') } catch { measureCanvas = null }
  }
  return measureCanvas
}
/** The width in px of ONE word as the table sets it (`font` is the CSS font shorthand without the family); an estimate where there is no canvas (tests, server). */
function wordWidth(word: string, font: string, spacing: number, fallback: number): number {
  const ctx = canvas()
  if (!ctx) return Math.ceil(word.length * fallback)
  try {
    ctx.font = `${font} ${getComputedStyle(document.body).fontFamily}`
    return Math.ceil(ctx.measureText(word).width + word.length * spacing)
  } catch { return Math.ceil(word.length * fallback) }
}
const wordsOf = (text: string) => text.split(/\s+/).filter(Boolean)
/**
 * The width in px of the LONGEST WORD of a column heading as the table sets it (11.5px, 700, uppercase, .04em). Headings wrap at spaces, so a figure
 * column only has to be as wide as that word plus its padding; an estimate where there is no canvas.
 */
export function headWordWidth(text: string): number {
  return Math.max(0, ...wordsOf(text.toUpperCase()).map(word => wordWidth(word, '700 11.5px', 0.46, 8.2)))
}
/** The width of the longest word of the names as the table sets them (13px, 600): the name column is never narrower than that word plus its padding. */
export function nameWordWidth(names: string[]): number {
  return Math.max(0, ...names.flatMap(wordsOf).map(word => wordWidth(word, '600 13px', 0, 7.6)))
}

export function hueOf(name: string) {
  return [...name].reduce((total, character) => total + character.charCodeAt(0), 0) % 6
}

/**
 * The people table in the Tasks screen's table style (its own CSS: .dw-table, hairline dividers, group bands such as
 * "Active today 3"). ONE column template serves the heading and every row (fixed layout over one colgroup with explicit widths).
 * Headings AND cells are centred (numbers too, in tabular figures; the group bands "Active today" stay left): a figure column is exactly as wide as
 * the longest word of its own heading plus padding (headings wrap at spaces, never cut), the name column is the remainder and never narrower than
 * its longest name word. The plan is computed from the columns that are shown, so it follows a column that appears or disappears.
 * A number with something behind it is a real button that opens its list; zero and missing are a light dash. An optional Total
 * row closes the table, in bold.
 */
export function FigureTable({ caption, cols, tailHead, tailWidth = 136, tailNumeric = false, groups, total, nameHeader = 'Recruiter', nameWidth, selectedId = '', onDrill, onPickName, scope, dense = false }: {
  caption: string
  cols: TableCol[]
  tailHead?: string
  tailWidth?: number
  /** The last column holds a number: its cells are plain figures (the Total column) rather than a pill or a meter. */
  tailNumeric?: boolean
  groups: FigureGroup[]
  total?: FigureRow
  nameHeader?: string
  /** The least the name column takes (px); it is never less than the longest word of the names plus padding either. */
  nameWidth?: number
  selectedId?: string
  onDrill: (metric: string, staffId: string | null) => void
  onPickName?: (id: string) => void
  scope: string
  dense?: boolean
  /** Kept for callers; the column plan now comes from each heading's measured width. */
  colWidth?: number
}) {
  const all: Cells = {}
  for (const group of groups) for (const row of group.rows) for (const [id, value] of Object.entries(row.cells)) all[`${row.id}:${id}`] = value
  if (total) for (const [id, value] of Object.entries(total.cells)) all[`total:${id}`] = value
  const flash = useChangedCells(`rows|${scope}`, all)
  // Column plan. The table is fixed-layout at 100% of its panel, so it always fits (no sideways scroll, no inline min-width). Every figure column
  // is exactly as wide as the longest word of its own heading (headings wrap at spaces, uppercase, measured) plus its padding, and no narrower than
  // its widest figure; the name column is the remainder and never less than its longest word. Nothing here is a per-screen number: it follows `cols`.
  const heads = [...cols.map(col => col.head ?? col.label), ...(tailHead ? [tailHead] : [])].map(headWordWidth)
  const names = [...groups.flatMap(group => group.rows), ...(total ? [total] : [])].map(row => row.name)
  const nameLeast = Math.max(nameWidth ?? (dense ? 120 : 140), nameWordWidth([nameHeader, ...names]) + 24)
  // When the figure columns and the name's least width do not fit the 1080px of the narrowest table, every figure column gives up some of its padding (never below 6px)
  const wanted = (pad: number) => heads.map((word, i) => Math.max(44, word + pad, i === cols.length && !tailNumeric ? tailWidth : 0))
  let pad = CELL_PAD
  while (pad > 6 && wanted(pad).reduce((sum, width) => sum + width, 0) + nameLeast > TABLE_MIN_WIDTH) pad -= 1
  const colWidths = wanted(pad).map(width => width + 'px')
  const tailClass = tailNumeric ? 'is-num' : 'is-tail'
  const bands = groups.length > 1
  let index = 0

  const figure = (row: FigureRow, col: TableCol, staffId: string | null) => {
    const value = row.cells[col.id]
    const shown = value != null && value > 0
    return <td key={col.id} data-label={col.head ?? col.label} className={cn('is-num', col.strong && 'is-strong', flash.has(`${staffId ?? 'total'}:${col.id}`) && 'is-flash')}>
      {shown && col.drill && !row.fixed
        ? <button type="button" className="dwr-link" onClick={() => onDrill(col.drill!, staffId)} aria-label={`${numberText(value)} ${col.label}. Show the list`}>{numberText(value)}</button>
        : shown ? <span className="dwr-plain">{numberText(value)}</span>
          : <span className="dw-none" aria-label={value === 0 ? '0' : 'not available'}>{'–'}</span>}
    </td>
  }
  return <ScrollEdges label={caption}>
    <table className={cn('dw-table dw-main dwr-table', dense && 'is-dense')} aria-label={caption}>
      <colgroup><col style={{ width: 'auto' }} />{colWidths.map((width, i) => <col key={i} style={{ width }} />)}</colgroup>
      <thead><tr>
        <th scope="col" className="dwr-name-head">{nameHeader}</th>
        {cols.map(col => <th key={col.id} scope="col" className="is-num" title={col.info ? (col.head ? `${col.label}. ${col.info}` : col.info) : col.label}>{col.head ?? col.label}</th>)}
        {tailHead && <th scope="col" className={tailClass}>{tailHead}</th>}
      </tr></thead>
      <tbody>
        {groups.map(group => <Fragment key={group.key}>
          {bands && <tr className={cn('dw-group', group.tone === 'done' && 'is-done')}>
            <th colSpan={cols.length + 1 + (tailHead ? 1 : 0)} scope="colgroup"><span className="dw-group-title">{group.icon}{group.title}<b className="tnum">{group.rows.length}</b></span></th>
          </tr>}
          {group.rows.map(row => {
            index += 1
            return <tr key={row.id} className={cn('dw-staff-row', index % 2 === 0 && 'is-alt', row.idle && 'is-idle', row.id === selectedId && 'is-active')}>
              <th scope="row" className="dwr-name">
                <span className="dwr-person">
                  <span className={cn('record-avatar', `avatar-hue-${hueOf(row.name)}`)} aria-hidden="true">{initials(row.name)}</span>
                  {onPickName && !row.fixed
                    ? <button type="button" className="dwr-who" onClick={() => onPickName(row.id)} aria-pressed={row.id === selectedId} title={row.id === selectedId ? 'Show all recruiters' : `Show only ${row.name}`}>{row.name}</button>
                    : <span className="dwr-who">{row.name}</span>}
                </span>
              </th>
              {cols.map(col => figure(row, col, row.id))}
              {tailHead && <td className={tailClass} data-label={tailHead}>{row.tail}</td>}
            </tr>
          })}
        </Fragment>)}
      </tbody>
      {total && <tfoot>
        <tr className="dwr-total">
          <th scope="row" className="dwr-name">{total.name}</th>
          {cols.map(col => figure(total, col, null))}
          {tailHead && <td className={tailClass} data-label={tailHead}>{total.tail}</td>}
        </tr>
      </tfoot>}
    </table>
  </ScrollEdges>
}
