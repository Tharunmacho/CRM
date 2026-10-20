import type { ReactNode } from 'react'
import { BrowserRouter } from 'react-router-dom'
import { StoreProvider } from '@/shared/state/crm-store'

/** Application-wide providers live here; feature pages consume their shared hooks. */
export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <BrowserRouter>
      <StoreProvider>{children}</StoreProvider>
    </BrowserRouter>
  )
}
