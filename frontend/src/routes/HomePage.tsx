import { useAuth } from '../lib/auth-context'

/** Empty state for a new chat. The composer and streaming arrive in the next milestone (M2). */
export function HomePage() {
  const { user } = useAuth()
  return (
    <div className="mx-auto flex min-h-screen max-w-chat flex-col items-center justify-center px-4 text-center">
      <h1 className="text-h1">Hello, {user?.display_name}</h1>
      <p className="mt-3 text-body-lg text-fg-muted">
        You are signed in. Chat is coming in the next milestone.
      </p>
    </div>
  )
}
