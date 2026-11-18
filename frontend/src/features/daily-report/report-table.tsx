import { Fragment, type ReactNode } from 'react'
import { cn } from '@/shared/lib/cn'
import { initials } from '@/shared/lib/format'
import { ScrollEdges, columnLayout } from '@/screens/tasks/daily-table'
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
export function FigureTable({ caption, cols, tailHead, tailWidth = 132, tailNumeric = false, groups, total, nameHeader = 'Recruiter', nameWidth, selectedId = '', onDrill, onPickName, scope, dense = false, colWidth }: {
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
  /** Width of a figure column that has none of its own (default 112, or 62 when dense). */
  colWidth?: number
}) {
  const all: Cells = {}
  for (const group of groups) for (const row of group.rows) for (const [id, value] of Object.entries(row.cells)) all[`${row.id}:${id}`] = value
  if (total) for (const [id, value] of Object.entries(total.cells)) all[`total:${id}`] = value
  const flash = useChangedCells(`rows|${scope}`, all)
  const widths = [{ width: nameWidth ?? (dense ? 190 : 230) }, ...cols.map(col => ({ width: col.width ?? colWidth ?? (dense ? 62 : 112) })), ...(tailHead ? [{ width: tailWidth }] : [])]
  const layout = columnLayout(widths)
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
  return <ScrollEdges label={`${caption}. Scrolls sideways on a small screen.`}>
    <table className={cn('dw-table dw-main dwr-table', dense && 'is-dense')} aria-label={caption} style={{ minWidth: layout.minWidth }}>
      <colgroup>{widths.map((w, i) => <col key={i} style={{ width: layout.share(w) }} />)}</colgroup>
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
