import { useEffect, type ReactNode } from 'react'
import type { AuthManager } from './auth'
import { AuthContext } from './auth-context'

/** Provides the AuthManager and signs in silently from the refresh cookie on first load. */
export function AuthProvider({ manager, children }: { manager: AuthManager; children: ReactNode }) {
  useEffect(() => {
    // Safe under React StrictMode's double-mount: concurrent bootstraps share one request.
    if (manager.getState().status === 'loading') void manager.bootstrap()
  }, [manager])
  return <AuthContext.Provider value={manager}>{children}</AuthContext.Provider>
}
