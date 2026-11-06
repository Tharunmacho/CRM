import { useEffect, type ReactNode } from 'react'
import { BrowserRouter } from 'react-router-dom'
import { StoreProvider } from '@/shared/state/crm-store'
import { installPrefetch } from '@/app/prefetch'

/** Application-wide providers live here; feature pages consume their shared hooks. */
export function AppProviders({ children }: { children: ReactNode }) {
  // Hovering or focusing a link warms the next screen's code (and its first shared reads) before the click.
  useEffect(() => installPrefetch(), [])
  return (
    <BrowserRouter>
      <StoreProvider>{children}</StoreProvider>
    </BrowserRouter>
  )
}
