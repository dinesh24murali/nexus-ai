import { Link } from 'react-router'

export function NotFoundPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center px-4 text-center">
      <h1 className="text-h2">Page not found</h1>
      <p className="mt-2 text-fg-muted">The page you are looking for does not exist.</p>
      <Link to="/" className="mt-6 text-primary underline underline-offset-2">
        Go home
      </Link>
    </main>
  )
}
