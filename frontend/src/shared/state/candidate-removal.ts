/**
 * Optimistic removal of one candidate from the workspace the store holds.
 *
 * Deleting a candidate on the server takes a moment, and the screens must not wait for it: the candidate leaves
 * the lists the instant the admin confirms, and comes back (with an error message) only if the server refuses.
 * The server removes the candidate's applications, tasks and remarks with them, clears the to-dos that linked to
 * them and drops the notifications that pointed at them; this mirrors exactly that so nothing is left dangling
 * on the screen. Plain data in, plain data out, so it is easy to test.
 */

export interface RemovableWorkspace {
  candidates: ReadonlyArray<{ id: string }>
  applications: ReadonlyArray<{ id: string; candidateId: string }>
  tasks: ReadonlyArray<{ id: string; candidateId: string }>
  remarks: ReadonlyArray<{ id: string; taskId: string }>
  todos: ReadonlyArray<{ id: string; linkTo?: string; linkLabel?: string }>
  notifications: ReadonlyArray<{ id: string; linkTo?: string }>
}

/** What was taken out, so a refused delete can put it back. */
export interface RemovedCandidate {
  id: string
  candidates: unknown[]
  applications: unknown[]
  tasks: unknown[]
  remarks: unknown[]
  notifications: unknown[]
  /** To-dos whose link was cleared, as they were. */
  todos: unknown[]
}

const candidateLink = (id: string) => `/candidates/${id}`
const taskLink = (id: string) => `/tasks?task=${id}`

/** The workspace without this candidate (same arrays when nothing referred to them), and what was removed. */
export function removeCandidate<T extends RemovableWorkspace>(data: T, id: string): { data: T; removed: RemovedCandidate } {
  const taskIds = new Set(data.tasks.filter((task) => task.candidateId === id).map((task) => task.id))
  const links = new Set([candidateLink(id), ...[...taskIds].map(taskLink)])
  const removed: RemovedCandidate = {
    id,
    candidates: data.candidates.filter((candidate) => candidate.id === id),
    applications: data.applications.filter((application) => application.candidateId === id),
    tasks: data.tasks.filter((task) => task.candidateId === id),
    remarks: data.remarks.filter((remark) => taskIds.has(remark.taskId)),
    notifications: data.notifications.filter((note) => note.linkTo !== undefined && links.has(note.linkTo)),
    todos: data.todos.filter((todo) => todo.linkTo === candidateLink(id)),
  }
  const keep = <Row,>(rows: ReadonlyArray<Row>, gone: unknown[]): Row[] => (gone.length ? rows.filter((row) => !gone.includes(row)) : (rows as Row[]))
  const next = {
    ...data,
    candidates: keep(data.candidates, removed.candidates),
    applications: keep(data.applications, removed.applications),
    tasks: keep(data.tasks, removed.tasks),
    remarks: keep(data.remarks, removed.remarks),
    notifications: keep(data.notifications, removed.notifications),
    todos: removed.todos.length
      ? data.todos.map((todo) => (todo.linkTo === candidateLink(id) ? { ...todo, linkTo: undefined, linkLabel: undefined } : todo))
      : data.todos,
  } as T
  return { data: next, removed }
}

/** Put a refused delete back. Anything the server has meanwhile sent again is not duplicated. */
export function restoreCandidate<T extends RemovableWorkspace>(data: T, removed: RemovedCandidate): T {
  const merge = <Row extends { id: string }>(rows: ReadonlyArray<Row>, back: unknown[]): Row[] => {
    if (!back.length) return rows as Row[]
    const present = new Set(rows.map((row) => row.id))
    return [...rows, ...(back as Row[]).filter((row) => !present.has(row.id))]
  }
  const todos = removed.todos.length
    ? data.todos.map((todo) => {
        const before = (removed.todos as Array<{ id: string }>).find((row) => row.id === todo.id)
        return before ? (before as typeof todo) : todo
      })
    : data.todos
  return {
    ...data,
    candidates: merge(data.candidates, removed.candidates),
    applications: merge(data.applications, removed.applications),
    tasks: merge(data.tasks, removed.tasks),
    remarks: merge(data.remarks, removed.remarks),
    notifications: merge(data.notifications, removed.notifications),
    todos,
  } as T
}

// Candidates deleted in this session. A list a screen fetched from the server a moment ago (the Data Bank page)
// may still contain them until its next fetch lands; screens drop these ids so a deleted person never reappears.
const deleted = new Set<string>()

export const markCandidateDeleted = (id: string) => { deleted.add(id) }
export const unmarkCandidateDeleted = (id: string) => { deleted.delete(id) }
export const isCandidateDeleted = (id: string) => deleted.has(id)

/** The rows without the candidates deleted in this session (the same array when none is in it). */
export function withoutDeleted<Row extends { id: string }>(rows: Row[]): Row[] {
  return deleted.size && rows.some((row) => deleted.has(row.id)) ? rows.filter((row) => !deleted.has(row.id)) : rows
}

/** A snapshot from the server that was read before a delete finished: take the deleted candidates out of it again. */
export function dropDeleted<T extends RemovableWorkspace>(data: T): T {
  let next = data
  for (const id of deleted) next = removeCandidate(next, id).data
  return next
}
