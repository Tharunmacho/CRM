/**
 * Keep object identity for data that did not change.
 *
 * Every refresh of the workspace parses a brand-new JSON tree. Putting it into state as is makes every screen that
 * reads any collection render again, and rebuilds the lookup index, even when a live event announced a change to
 * something else (or to nothing that this account can see). `reconcile` compares the new snapshot with the one on
 * screen and hands back the OLD objects wherever the content is identical, so React's usual identity checks
 * (memo, useMemo, effect dependencies) skip the work. If nothing differs at all, the old snapshot itself is returned
 * and the caller can skip the state update.
 *
 * Content comparison is JSON text. A record's text is computed once per object (cached by identity), so a refresh
 * costs one serialization per new record and none for the records already on screen.
 */
const texts = new WeakMap<object, string>()

function textOf(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'undefined'
  let text = texts.get(value)
  if (text === undefined) {
    text = JSON.stringify(value)
    texts.set(value, text)
  }
  return text
}

function hasId(item: unknown): item is { id: string } {
  return typeof item === 'object' && item !== null && typeof (item as { id?: unknown }).id === 'string'
}

/** `next` as an array that reuses the objects of `previous` wherever they are identical; `previous` itself if all are. */
export function reconcileList<T>(previous: readonly T[] | undefined, next: readonly T[]): readonly T[] {
  if (!previous) return next
  const byId = new Map<string, T>()
  let keyed = true
  for (const item of previous) {
    if (hasId(item)) byId.set(item.id, item)
    else { keyed = false; break }
  }
  let identical = previous.length === next.length
  const merged = next.map((item, index) => {
    // Records with an id are matched by id (rows move when sorted differently); anything else by position.
    const old = keyed && hasId(item) ? byId.get(item.id) : previous[index]
    if (old !== undefined && (old === item || textOf(old) === textOf(item))) {
      if (previous[index] !== old) identical = false
      return old
    }
    identical = false
    return item
  })
  return identical ? previous : merged
}

/** Reconcile every field of a snapshot: arrays per record, other values by content. */
export function reconcile<S extends object>(previous: S, next: S): S {
  let identical = true
  const merged: Record<string, unknown> = {}
  for (const key of Object.keys(next) as (keyof S & string)[]) {
    const before = previous[key]
    const after = next[key]
    let value: unknown = after
    if (Array.isArray(after) && Array.isArray(before)) value = reconcileList(before, after)
    else if (before !== undefined && before !== after && textOf(before) === textOf(after)) value = before
    if (value !== before) identical = false
    merged[key] = value
  }
  if (Object.keys(previous).some((key) => !(key in next))) identical = false
  return identical ? previous : (merged as S)
}

/** `next`, or `previous` itself when both have the same content. */
export function keepIfSame<T>(previous: T | null | undefined, next: T): T {
  return previous !== null && previous !== undefined && previous !== next && textOf(previous) === textOf(next) ? previous : next
}
