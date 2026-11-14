import type { Theme } from './theme'

/**
 * Brand artwork at twice its largest display size (the 768x400 originals stay in public/brand for emails and print).
 * Only the file for the current theme is ever requested: hidden light/dark twins are still downloaded by the browser.
 */
export const LOGO_WIDTH = 480
export const LOGO_HEIGHT = 250
export const MARK_WIDTH = 72
export const MARK_HEIGHT = 145

export function brandLogoSrc(theme: Theme): string {
  return theme === 'dark' ? '/brand/care-india-logo-tight-dark-480.webp' : '/brand/care-india-logo-tight-480.webp'
}

export function brandMarkSrc(theme: Theme): string {
  return theme === 'dark' ? '/brand/care-india-mark-dark-fit-144.webp' : '/brand/care-india-mark-fit-144.webp'
}
