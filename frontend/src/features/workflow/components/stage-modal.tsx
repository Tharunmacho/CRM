import { lazy, Suspense, type ComponentProps } from 'react'
import type { StageModal as StageModalBody } from './stage-modal-body'

/**
 * The application dialog is large (about 60 KB of code and 40 KB of styles) and opens only on a click, yet nine screens
 * import it. This wrapper keeps those imports as they are while the dialog itself downloads when first needed (and is
 * fetched in the background once the page is idle, see app/prefetch.ts), so no screen pays for it on first load.
 */
const loadBody = () => import('./stage-modal-body').then((module) => ({ default: module.StageModal }))
const Body = lazy(loadBody)

/** Download the dialog now (idle time, or a hover on something that opens it). */
export const preloadStageModal = () => { void loadBody().catch(() => {}) }

export function StageModal(props: ComponentProps<typeof StageModalBody>) {
  return (
    <Suspense fallback={null}>
      <Body {...props} />
    </Suspense>
  )
}
