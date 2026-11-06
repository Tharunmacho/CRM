/**
 * Company web addresses. The server saves one tidy `https://...` text (see normalize_website in routers/data.py);
 * these helpers apply the same rule while typing and make a stored value safe to show as a link.
 */

export const WEBSITE_MAX = 255

const HOST = /^(?=.{1,253}$)([a-z0-9\u00a1-\uffff]([a-z0-9\u00a1-\uffff-]{0,61}[a-z0-9\u00a1-\uffff])?\.)+[a-z\u00a1-\uffff][a-z0-9\u00a1-\uffff-]{0,61}[a-z0-9\u00a1-\uffff]$/i

const BAD_ADDRESS = 'Enter a valid web address, such as www.example.com'

/**
 * The address a user typed as the text the server would save, or the reason it is refused.
 * Blank is fine (the field is optional); a bare domain gets https://; spaces, script schemes
 * (javascript:, data:, mailto:...), user:password@ parts and hosts without a dot are refused.
 */
export function checkWebsite(raw: string | null | undefined): { value: string; error: string } {
  const text = (raw ?? '').trim()
  if (!text) return { value: '', error: '' }
  if (/\s/.test(text)) return { value: text, error: 'Enter the web address without spaces, such as www.example.com' }
  let full = text
  const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(text)
  if (scheme) {
    const slashes = text.slice(scheme[0].length, scheme[0].length + 2) === '//'
    if (slashes ? !/^https?$/i.test(scheme[1]) : !/^[^/:?#]+:\d+(\/|$|\?|#)/.test(text)) {
      return { value: text, error: 'Only http:// or https:// addresses are accepted' }
    }
    if (!slashes) full = 'https://' + text
  } else {
    full = 'https://' + text
  }
  let url: URL
  try { url = new URL(full) } catch { return { value: text, error: BAD_ADDRESS } }
  if (url.username || url.password || !HOST.test(url.hostname)) return { value: text, error: BAD_ADDRESS }
  const path = url.pathname === '/' && !url.search && !url.hash ? '' : url.pathname
  const value = `${url.protocol}//${url.host}${path}${url.search}${url.hash}`
  if (value.length > WEBSITE_MAX) return { value: text, error: `The address is too long (${WEBSITE_MAX} characters at most)` }
  return { value, error: '' }
}

/** A stored website as a link target: only http(s) addresses qualify, anything else (older or odd data) shows as plain text. */
export function websiteHref(value: string | null | undefined): string | null {
  const text = (value ?? '').trim()
  if (!text) return null
  const { value: tidy, error } = checkWebsite(text)
  return error ? null : tidy
}

/** The address without https:// and a lone trailing slash, for compact display. */
export const websiteLabel = (value: string | null | undefined) =>
  (value ?? '').trim().replace(/^https?:\/\//i, '').replace(/\/$/, '')
