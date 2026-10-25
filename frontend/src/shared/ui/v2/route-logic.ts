/** Pure rules behind RouteTrack (kept free of React so they can be unit tested). */
export type RouteStageDef = { no: number; title: string }
export type RouteNodeState = 'done' | 'current' | 'todo' | 'closed'


export const STATE_TEXT: Record<RouteNodeState, string> = { done: 'done', current: 'current stage', todo: 'not started', closed: 'ended here' }

export function routeStates(stages: ReadonlyArray<RouteStageDef>, current?: number, completed?: number | number[], closed?: boolean | number): RouteNodeState[] {
  const closedNo = closed === true ? current : typeof closed === 'number' ? closed : undefined
  const done = new Set<number>(
    Array.isArray(completed)
      ? completed
      : typeof completed === 'number'
        ? stages.filter((s) => s.no <= completed).map((s) => s.no)
        : stages.filter((s) => current !== undefined && s.no < current).map((s) => s.no),
  )
  return stages.map((s) => (s.no === closedNo ? 'closed' : done.has(s.no) ? 'done' : s.no === current ? 'current' : 'todo'))
}


/** "3 more steps": the only thing a route says about stages that are not reached yet when upcoming stages are hidden. */
export function upcomingText(states: ReadonlyArray<RouteNodeState>): string {
  const count = states.filter((state) => state === 'todo').length
  return count > 0 ? `${count} more ${count === 1 ? 'step' : 'steps'}` : ''
}

/** The accessible name of the whole route. With hideUpcoming it never names a stage that is not reached yet. */
export function routeSummary(label: string, stages: ReadonlyArray<RouteStageDef>, states: ReadonlyArray<RouteNodeState>, hideUpcoming = false): string {
  const index = states.findIndex((state) => state === 'current' || state === 'closed')
  const done = states.filter((state) => state === 'done').length
  const base = index >= 0
    ? `${label}: stage ${stages[index].no} of ${stages.length}, ${stages[index].title}${states[index] === 'closed' ? ' (ended here)' : ''}`
    : `${label}: ${done} of ${stages.length} stages done`
  const more = hideUpcoming ? upcomingText(states) : ''
  return more ? `${base}, ${more}` : base
}
