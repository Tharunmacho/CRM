import type { ReactNode } from 'react'
import { Check, X } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { STAGES } from '@/shared/workflow/stages'
import { routeStates, routeSummary, STATE_TEXT, upcomingText, type RouteNodeState, type RouteStageDef } from './route-logic'

/**
 * RouteTrack: the 11-stage placement route as a horizontal line of nodes.
 *
 * Props
 *  - stages?    { no, title }[]  default: the real STAGES list (src/shared/workflow/stages.ts)
 *  - current?   number           stage number the application is at (1..11); omit for "not started" / "finished"
 *  - completed? number | number[] a count n = stages 1..n are done; an array = exactly those stage numbers; default = every stage before `current`
 *  - closed?    boolean | number true = the current stage ended the application; a number = the stage it closed at (shows a cross node)
 *  - size       'xs' | 'sm' | 'md' | 'lg'   xs/sm are small fixed-width strips for rows; md/lg fill their container
 *  - labels?    boolean          show stage titles (md/lg). Narrow containers (< 760px) show only the current stage name
 *  - onSelect?  (no) => void     makes every node a button
 *  - label?     string           accessible name, default "Placement route"
 *  - hideUpcoming? boolean       upcoming stages become anonymous hollow nodes: no title, no label, no number, and the
 *                                accessible text only says "n more steps" (PRD: future stages are not shown)
 */
export { routeStates }
export type { RouteStageDef, RouteNodeState }

export interface RouteTrackProps {
  stages?: ReadonlyArray<RouteStageDef>
  current?: number
  completed?: number | number[]
  closed?: boolean | number
  size?: 'xs' | 'sm' | 'md' | 'lg'
  labels?: boolean
  onSelect?: (no: number) => void
  label?: string
  hideUpcoming?: boolean
  className?: string
}

export function RouteTrack({ stages = STAGES, current, completed, closed, size = 'md', labels = false, onSelect, label = 'Placement route', hideUpcoming = false, className }: RouteTrackProps) {
  const states = routeStates(stages, current, completed, closed)
  const currentIndex = states.findIndex((s) => s === 'current' || s === 'closed')
  const currentStage = currentIndex >= 0 ? stages[currentIndex] : undefined
  const showLabels = labels && (size === 'md' || size === 'lg')
  const moreText = hideUpcoming ? upcomingText(states) : ''
  const summary = routeSummary(label, stages, states, hideUpcoming)
  return (
    <div className={cn('v2-route', `v2-route-${size}`, showLabels && 'has-labels', className)}>
      <ol className="v2-route-list" aria-label={summary}>
        {stages.map((stage, index) => {
          const state = states[index]
          const prevDone = index > 0 && states[index - 1] === 'done'
          const hidden = hideUpcoming && state === 'todo'
          const inner = hidden ? (
            <>
              <span className="v2-route-node" aria-hidden="true" />
              <span className="sr-only">Upcoming step</span>
            </>
          ) : (
            <>
              <span className="v2-route-node" aria-hidden="true">
                {size === 'lg'
                  ? (state === 'done' ? <Check size={13} strokeWidth={3} /> : state === 'closed' ? <X size={13} strokeWidth={3} /> : <b>{stage.no}</b>)
                  : state === 'closed' ? <X size={size === 'md' ? 10 : 7} strokeWidth={3.6} /> : null}
              </span>
              {showLabels && <span className="v2-route-label" aria-hidden="true">{stage.title}</span>}
              <span className="sr-only">{`Stage ${stage.no}, ${stage.title}, ${STATE_TEXT[state]}`}</span>
            </>
          )
          return (
            <li
              key={stage.no}
              className="v2-route-cell"
              data-state={state}
              data-prev-done={prevDone ? '' : undefined}
              data-first={index === 0 ? '' : undefined}
              data-last={index === stages.length - 1 ? '' : undefined}
              aria-current={state === 'current' ? 'step' : undefined}
              title={hidden ? undefined : `${stage.no}. ${stage.title}`}
            >
              {onSelect
                ? <button type="button" className="v2-route-hit" onClick={() => onSelect(stage.no)}>{inner}</button>
                : <span className="v2-route-hit">{inner}</span>}
            </li>
          )
        })}
      </ol>
      {showLabels && currentStage && <p className="v2-route-caption" aria-hidden="true">Stage {currentStage.no} of {stages.length}<span>{currentStage.title}</span></p>}
      {showLabels && moreText && <p className="v2-route-more">{moreText}</p>}
    </div>
  )
}

/**
 * RouteTimeline: a vertical route for histories (activity, audit, stage records).
 *
 * Props
 *  - items: { id?, title, meta?, children?, state?, icon? }[]
 *      state 'done' (filled), 'current' (ring), 'todo' (hollow), 'closed' (cross), 'note' (small dot, default)
 *      meta = small text on the right (a time); children = detail text/blocks under the title
 *  - label?: accessible name of the list
 */
export interface RouteTimelineItem {
  id?: string
  title: ReactNode
  meta?: ReactNode
  children?: ReactNode
  state?: 'done' | 'current' | 'todo' | 'closed' | 'note'
  icon?: ReactNode
}

export function RouteTimeline({ items, label = 'History', className }: { items: RouteTimelineItem[]; label?: string; className?: string }) {
  return (
    <ol className={cn('v2-timeline', className)} aria-label={label}>
      {items.map((item, index) => {
        const state = item.state ?? 'note'
        return (
          <li key={item.id ?? index} className="v2-timeline-item" data-state={state} data-last={index === items.length - 1 ? '' : undefined}>
            <span className="v2-timeline-node" aria-hidden="true">
              {item.icon ?? (state === 'done' ? <Check size={11} strokeWidth={3.2} /> : state === 'closed' ? <X size={11} strokeWidth={3.2} /> : null)}
            </span>
            <div className="v2-timeline-body">
              <div className="v2-timeline-head">
                <span className="v2-timeline-title">{item.title}</span>
                {item.meta != null && <span className="v2-timeline-meta">{item.meta}</span>}
              </div>
              {item.children != null && <div className="v2-timeline-detail">{item.children}</div>}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
