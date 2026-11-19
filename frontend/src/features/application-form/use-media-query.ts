import { useEffect, useState } from 'react'

/** Whether a CSS media query matches right now, and follows it as the window is resized. False where `matchMedia` does not exist. */
export function useMediaQuery(query: string): boolean {
  const read = () => (typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(query).matches : false)
  const [matches, setMatches] = useState(read)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const list = window.matchMedia(query)
    const update = () => setMatches(list.matches)
    update()
    list.addEventListener('change', update)
    return () => list.removeEventListener('change', update)
  }, [query])
  return matches
}

/** From this width the page has room to show Personal beside Passport (and Education over Experience) without squeezing the fields. */
export const WIDE_DETAILS_QUERY = '(min-width: 1360px)'
