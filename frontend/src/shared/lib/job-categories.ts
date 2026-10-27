import type { JobCategory } from '@/shared/model/types'

/**
 * Pure helpers for the managed job category list (Users > Agency settings) and the Category box on a job title.
 * Nothing here knows an agency's categories: they arrive from the database through the store.
 */

export const CATEGORY_MIN = 2
export const CATEGORY_MAX = 120

/** Trimmed, with runs of spaces collapsed: the same rule the server applies before it saves a name. */
export const cleanCategoryName = (name: string) => name.trim().replace(/\s+/g, ' ')

const categoryKey = (name: string) => cleanCategoryName(name).toLowerCase()

export const sortedCategories = (categories?: JobCategory[] | null) =>
  [...(categories ?? [])].sort((a, b) => a.sortOrder - b.sortOrder || a.name.localeCompare(b.name))

/** Distinct category texts already on job titles (ignoring case, first spelling wins), most used first, then A to Z. */
export function usedCategories(values: Iterable<string | null | undefined>): string[] {
  const counts = new Map<string, { text: string; count: number }>()
  for (const raw of values) {
    const text = cleanCategoryName(raw ?? '')
    if (!text) continue
    const entry = counts.get(text.toLowerCase())
    if (entry) entry.count += 1
    else counts.set(text.toLowerCase(), { text, count: 1 })
  }
  return [...counts.values()].sort((a, b) => b.count - a.count || a.text.localeCompare(b.text)).map((entry) => entry.text)
}

/**
 * What the Category box on a job title suggests: the active managed categories first, in the agency's order, then any
 * other category already used on a job title (typed before the list existed, or by someone else), most used first.
 * Names are compared ignoring case, and a category the agency switched off is not offered again.
 */
export function categorySuggestions(categories: JobCategory[] | undefined | null, used: Iterable<string | null | undefined>): string[] {
  const managed = sortedCategories(categories)
  const known = new Set(managed.map((item) => categoryKey(item.name)))
  const offered = managed.filter((item) => item.active).map((item) => cleanCategoryName(item.name))
  return [...offered, ...usedCategories(used).filter((name) => !known.has(categoryKey(name)))]
}

/** The same rules the server applies, so a mistake is shown beside the box before anything is sent. */
export function categoryNameError(name: string, existing: JobCategory[], ignoreId?: string): string | undefined {
  const text = cleanCategoryName(name)
  if (!text) return 'Enter the category name.'
  if (text.length < CATEGORY_MIN) return `Use at least ${CATEGORY_MIN} characters.`
  if (text.length > CATEGORY_MAX) return `Use ${CATEGORY_MAX} characters or fewer.`
  if (existing.some((item) => item.id !== ignoreId && categoryKey(item.name) === text.toLowerCase())) return 'That category is already in the list.'
  return undefined
}
