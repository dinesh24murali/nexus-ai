import { Loader2 } from 'lucide-react'
import { Navigate, Outlet, useLocation } from 'react-router'
import { useAuth } from '../lib/auth-context'

function Splash() {
  return (
    <div
      role="status"
      aria-label="Loading"
      className="flex min-h-screen items-center justify-center"
    >
      <Loader2 className="size-6 animate-spin text-fg-muted" aria-hidden />
    </div>
  )
}

/** Protected routes: unauthenticated users go to /login and come back afterwards. */
export function RequireAuth() {
  const { status } = useAuth()
  const location = useLocation()
  if (status === 'loading') return <Splash />
  if (status === 'anonymous') {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }
  return <Outlet />
}

/** /login and /register: signed-in users are sent home. */
export function PublicOnly() {
  const { status } = useAuth()
  if (status === 'loading') return <Splash />
  if (status === 'authenticated') return <Navigate to="/" replace />
  return <Outlet />
}
