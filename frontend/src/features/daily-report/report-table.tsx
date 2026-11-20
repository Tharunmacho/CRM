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
const TABLE_MIN_WIDTH = 1080
let headCanvas: CanvasRenderingContext2D | null | undefined
/** The width in px of a column heading as the table sets it (11.5px, 700, uppercase, .04em); an estimate where there is no canvas (tests, server). */
function headWidth(text: string): number {
  if (headCanvas === undefined) {
    try { headCanvas = typeof document === 'undefined' ? null : document.createElement('canvas').getContext('2d') } catch { headCanvas = null }
  }
  const upper = text.toUpperCase()
  if (!headCanvas) return Math.ceil(upper.length * 8.2)
  try {
    headCanvas.font = `700 11.5px ${getComputedStyle(document.body).fontFamily}`
    return Math.ceil(headCanvas.measureText(upper).width + upper.length * 0.46)
  } catch { return Math.ceil(upper.length * 8.2) }
}

function hueOf(name: string) {
  return [...name].reduce((total, character) => total + character.charCodeAt(0), 0) % 6
}

/**
 * The people table in the Tasks screen's table style (its own CSS: .dw-table, hairline dividers, group bands such as
 * "Active today 3"). ONE column template serves the heading and every row (fixed layout over one colgroup with explicit widths),
 * names are left, every figure and its heading are right-aligned with the same padding, so numbers sit under their headings.
 * A number with something behind it is a real button that opens its list; zero and missing are a light dash. An optional Total
 * row closes the table, in bold.
 */
export function FigureTable({ caption, cols, tailHead, tailWidth = 124, tailNumeric = false, groups, total, nameHeader = 'Recruiter', nameWidth, selectedId = '', onDrill, onPickName, scope, dense = false }: {
  caption: string
  cols: TableCol[]
  tailHead?: string
  tailWidth?: number
  /** The last column holds a number: heading and cells are right-aligned like the figures. */
  tailNumeric?: boolean
  groups: FigureGroup[]
  total?: FigureRow
  nameHeader?: string
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
  // is exactly as wide as its own heading needs (one line, uppercase, measured), spread a little wider when there is room; the name column is
  // the remainder (names wrap at spaces). The plan is expressed against the narrowest width at which this is a table (1080px).
  const need = cols.map(col => Math.max(40, headWidth(col.head ?? col.label) + 18))
  if (tailHead) need.push(Math.max(56, headWidth(tailHead) + 18, tailWidth))
  const nameTarget = nameWidth ?? (dense ? 190 : 230)
  const grow = Math.min(1.3, Math.max(1, (TABLE_MIN_WIDTH - nameTarget) / need.reduce((sum, width) => sum + width, 0)))
  const share = (width: number) => `${((width * grow) / TABLE_MIN_WIDTH * 100).toFixed(3)}%`
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
      <colgroup><col style={{ width: 'auto' }} />{need.map((width, i) => <col key={i} style={{ width: share(width) }} />)}</colgroup>
      <thead><tr>
        <th scope="col" className="is-left">{nameHeader}</th>
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
              <th scope="row" className="is-left dwr-name">
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
          <th scope="row" className="is-left dwr-name">{total.name}</th>
          {cols.map(col => figure(total, col, null))}
          {tailHead && <td className={tailClass} data-label={tailHead}>{total.tail}</td>}
        </tr>
      </tfoot>}
    </table>
  </ScrollEdges>
}
