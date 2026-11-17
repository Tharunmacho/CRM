import { memo } from 'react'
import { Search, X } from 'lucide-react'
import { FILTER_LABEL, type ListFilter } from '../import-view'

const ORDER: ListFilter[] = ['all', 'attention', 'ready', 'imported']

/** For long lists: filter chips with their counts, and a search over names, folders and file names. */
export const ListToolbar = memo(function ListToolbar({ filter, counts, onFilter, query, onQuery }: {
  filter: ListFilter
  counts: Record<ListFilter, number>
  onFilter: (value: ListFilter) => void
  query: string
  onQuery: (value: string) => void
}) {
  return (
    <div className="ic-toolbar">
      <div className="ic-filters" role="group" aria-label="Show">
        {ORDER.filter((key) => key === 'all' || counts[key] > 0 || key === filter).map((key) => (
          <button key={key} type="button" className={`ic-filter${filter === key ? ' is-on' : ''}${key === 'attention' && counts.attention > 0 ? ' is-warn' : ''}`}
            aria-pressed={filter === key} onClick={() => onFilter(key)}>
            {FILTER_LABEL[key]} <b>{counts[key]}</b>
          </button>
        ))}
      </div>
      <label className="ic-search">
        <Search aria-hidden="true" />
        <input type="search" value={query} onChange={(e) => onQuery(e.target.value)} placeholder="Search by name or file" aria-label="Search candidates" />
        {query && <button type="button" aria-label="Clear the search" onClick={() => onQuery('')}><X aria-hidden="true" /></button>}
      </label>
    </div>
  )
})
