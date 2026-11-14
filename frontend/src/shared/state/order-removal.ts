/**
 * Removal of one job (order) from the workspace the store holds, once the server has confirmed the delete.
 * The server removes the job's applications with their tasks and remarks, clears the to-dos that linked to the job
 * and drops the notifications that pointed at the job or its tasks; this mirrors that so nothing is left dangling
 * on the screen and every count that is worked out from these lists drops at once. Plain data in, plain data out.
 */

export interface OrderWorkspace {
  orders: ReadonlyArray<{ id: string }>
  applications: ReadonlyArray<{ id: string; orderId: string }>
  tasks: ReadonlyArray<{ id: string; applicationId?: string }>
  remarks: ReadonlyArray<{ id: string; taskId: string }>
  todos: ReadonlyArray<{ id: string; linkTo?: string; linkLabel?: string }>
  notifications: ReadonlyArray<{ id: string; linkTo?: string }>
}

export function removeOrder<T extends OrderWorkspace>(data: T, id: string): T {
  const applicationIds = new Set(data.applications.filter((application) => application.orderId === id).map((application) => application.id))
  const taskIds = new Set(data.tasks.filter((task) => task.applicationId !== undefined && applicationIds.has(task.applicationId)).map((task) => task.id))
  const link = `/orders/${id}`
  const links = new Set([link, ...[...taskIds].map((taskId) => `/tasks?task=${taskId}`)])
  return {
    ...data,
    orders: data.orders.filter((order) => order.id !== id),
    applications: applicationIds.size ? data.applications.filter((application) => !applicationIds.has(application.id)) : data.applications,
    tasks: taskIds.size ? data.tasks.filter((task) => !taskIds.has(task.id)) : data.tasks,
    remarks: taskIds.size ? data.remarks.filter((remark) => !taskIds.has(remark.taskId)) : data.remarks,
    notifications: data.notifications.some((note) => note.linkTo !== undefined && links.has(note.linkTo))
      ? data.notifications.filter((note) => note.linkTo === undefined || !links.has(note.linkTo))
      : data.notifications,
    todos: data.todos.some((todo) => todo.linkTo === link)
      ? data.todos.map((todo) => (todo.linkTo === link ? { ...todo, linkTo: undefined, linkLabel: undefined } : todo))
      : data.todos,
  }
}
