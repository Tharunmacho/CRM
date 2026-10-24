import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { CornerDownLeft, FileText, Search, User, X } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { useStore } from '@/shared/state/crm-store'

export interface SearchPage { label: string; to: string; icon: typeof Search }

/** ⌘K / Ctrl+K palette: jump to a page or a candidate without leaving the keyboard. */
export function CommandSearch({ open, onClose, pages }: { open: boolean; onClose: () => void; pages: SearchPage[] }) {
  const store = useStore()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!open) return
    setQuery(''); setActive(0)
    const frame = requestAnimationFrame(() => input.current?.focus())
    return () => cancelAnimationFrame(frame)
  }, [open])

  const results = useMemo(() => {
    const text = query.trim().toLowerCase()
    const pageHits = pages.filter((page) => !text || page.label.toLowerCase().includes(text)).map((page) => ({ key: `p:${page.to}`, label: page.label, detail: 'Page', to: page.to, kind: 'page' as const, icon: page.icon }))
    if (!text) return pageHits.slice(0, 8)
    const people = store.candidates
      .filter((candidate) => (store.can('work_any_candidate') || candidate.ownerId === store.me.id)
        && `${candidate.name} ${candidate.ref} ${candidate.phone} ${candidate.email ?? ''}`.toLowerCase().includes(text))
      .slice(0, 6)
      .map((candidate) => ({ key: `c:${candidate.id}`, label: candidate.name, detail: [candidate.ref, candidate.phone].filter(Boolean).join(' · '), to: `/candidates/${candidate.id}`, kind: 'candidate' as const, icon: User }))
    return [...people, ...pageHits].slice(0, 9)
  }, [query, pages, store])

  useEffect(() => { setActive(0) }, [query])

  if (!open) return null
  const choose = (to: string) => { onClose(); navigate(to) }
  const onKey = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape') { event.preventDefault(); onClose() }
    else if (event.key === 'ArrowDown') { event.preventDefault(); setActive((value) => Math.min(value + 1, results.length - 1)) }
    else if (event.key === 'ArrowUp') { event.preventDefault(); setActive((value) => Math.max(value - 1, 0)) }
    else if (event.key === 'Enter' && results[active]) { event.preventDefault(); choose(results[active].to) }
  }

  return createPortal(
    <div className="cmdk-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <div className="cmdk" role="dialog" aria-modal="true" aria-label="Search workspace" onKeyDown={onKey}>
        <div className="cmdk-input">
          <Search size={17} aria-hidden="true" />
          <input ref={input} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search candidates or pages" aria-label="Search" aria-controls="cmdk-results" autoComplete="off" />
          <kbd>Esc</kbd>
          <button type="button" className="cmdk-close" onClick={onClose} aria-label="Close search"><X size={20} aria-hidden="true" /></button>
        </div>
        <ul id="cmdk-results" className="cmdk-results scrollbar-thin" role="listbox">
          {results.length === 0 && <li className="cmdk-empty">No matches for “{query}”. Try a name, phone number or page.</li>}
          {results.map((result, index) => (
            <li key={result.key} role="option" aria-selected={index === active}>
              <button type="button" className={cn('cmdk-item', index === active && 'is-active')} onMouseEnter={() => setActive(index)} onClick={() => choose(result.to)}>
                <span className="cmdk-icon"><result.icon size={15} aria-hidden="true" /></span>
                <span className="cmdk-copy"><strong>{result.label}</strong><small>{result.detail}</small></span>
                {index === active && <CornerDownLeft size={14} aria-hidden="true" />}
              </button>
            </li>
          ))}
        </ul>
        <footer className="cmdk-foot"><span className="cmdk-keys"><kbd>↑</kbd><kbd>↓</kbd> to move</span><span className="cmdk-keys"><kbd>Enter</kbd> to open</span><span><FileText size={12} aria-hidden="true" /> Staff members can only find their own candidates</span></footer>
      </div>
    </div>,
    document.body,
  )
}
