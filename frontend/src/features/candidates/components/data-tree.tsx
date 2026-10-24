import { useState, type ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { buildTree, type TreeNode, type TreeOptions } from '../ocr-view'

/**
 * A closed-by-default block whose body is only mounted once opened, so a long list of
 * pages or a large JSON tree costs nothing until someone reads it.
 */
export function Disclosure({
  summary,
  meta,
  children,
  defaultOpen = false,
  className,
}: {
  summary: ReactNode
  meta?: ReactNode
  children: ReactNode
  defaultOpen?: boolean
  className?: string
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className={className}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 py-3 text-left text-[14px] font-semibold text-ink-700 hover:text-[color:var(--primary)]"
      >
        <ChevronRight className={cn('h-4 w-4 shrink-0 text-ink-500 transition-transform', open && 'rotate-90')} />
        <span className="min-w-0 break-words">{summary}</span>
        {meta && <span className="ml-auto shrink-0 pl-3 text-[13px] font-normal text-ink-500">{meta}</span>}
      </button>
      {open && <div>{children}</div>}
    </div>
  )
}

function Nodes({ nodes, depth }: { nodes: TreeNode[]; depth: number }) {
  return (
    <dl className={cn('space-y-2.5', depth > 0 && 'mt-1.5 border-l border-rule pl-4')}>
      {nodes.map((n, i) => (
        <div key={`${n.label}-${i}`} className="min-w-0">
          {n.kind === 'value' ? (
            <div className="grid gap-x-4 sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)]">
              <dt className="text-[13px] text-ink-500">{n.label}</dt>
              <dd className={cn('break-words text-[14px] font-medium text-ink-700', n.mono && 'whitespace-pre-wrap font-mono text-xs')}>{n.text}</dd>
            </div>
          ) : (
            <>
              <dt className="text-[13px] font-semibold text-ink-600">
                {n.list ? `${n.label} (${n.children.length})` : n.label}
              </dt>
              <dd>
                <Nodes nodes={n.children} depth={depth + 1} />
              </dd>
            </>
          )}
        </div>
      ))}
    </dl>
  )
}

/** Renders a whole stored payload as a labelled key/value tree. Nothing is truncated. */
export function DataTree({ value, options }: { value: unknown; options?: TreeOptions }) {
  const nodes = buildTree(value, options)
  if (nodes.length === 0) return <p className="text-[14px] text-ink-500">Nothing was stored.</p>
  return <Nodes nodes={nodes} depth={0} />
}
