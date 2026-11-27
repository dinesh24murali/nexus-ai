import { createContext, useContext, useSyncExternalStore } from 'react'
import type { AuthManager, AuthState } from './auth'

export const AuthContext = createContext<AuthManager | null>(null)

export function useAuthManager(): AuthManager {
  const manager = useContext(AuthContext)
  if (!manager) throw new Error('useAuth must be used inside <AuthProvider>')
  return manager
}

/** Current auth state; re-renders on change. Actions live on the manager. */
export function useAuth(): AuthState & { manager: AuthManager } {
  const manager = useAuthManager()
  const state = useSyncExternalStore(manager.subscribe, manager.getState, manager.getState)
  return { ...state, manager }
}
