import { useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import { Alert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { Field } from '../components/ui/Field'
import { useAuth } from '../lib/auth-context'
import { describeProblem } from '../lib/messages'
import { AuthLayout } from './AuthLayout'

export function LoginPage() {
  const { manager, sessionExpired } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from ?? '/'

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await manager.login(email.trim(), password)
      void navigate(from, { replace: true })
    } catch (e) {
      setError(describeProblem(e).message)
      setSubmitting(false)
    }
  }

  return (
    <AuthLayout title="Sign in">
      {sessionExpired && !error && (
        <Alert variant="info" className="mb-4">
          Your session expired. Please sign in again.
        </Alert>
      )}
      {error && (
        <Alert variant="error" className="mb-4">
          {error}
        </Alert>
      )}
      <form onSubmit={onSubmit} className="space-y-4" noValidate={false}>
        <Field
          label="Email"
          type="email"
          name="email"
          autoComplete="email"
          required
          autoFocus
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <Field
          label="Password"
          type="password"
          name="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <Button type="submit" className="w-full" loading={submitting}>
          Sign in
        </Button>
      </form>
      <p className="mt-6 text-center text-body-sm text-fg-muted">
        Have an invite?{' '}
        <Link to="/register" className="text-primary underline underline-offset-2">
          Create an account
        </Link>
      </p>
    </AuthLayout>
  )
}
