import { Check } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { Alert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { Field } from '../components/ui/Field'
import { config, type RegistrationMode } from '../config'
import { useAuth } from '../lib/auth-context'
import { cn } from '../lib/cn'
import { describeProblem, passwordStrength, type FormError } from '../lib/messages'
import { AuthLayout } from './AuthLayout'

const MIN_PASSWORD = 10

export function RegisterPage({
  registrationMode = config.registrationMode,
}: {
  registrationMode?: RegistrationMode
}) {
  const { manager } = useAuth()
  const navigate = useNavigate()
  const [params] = useSearchParams()

  const [invite, setInvite] = useState(params.get('invite') ?? '')
  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<FormError | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const strength = passwordStrength(password)
  const longEnough = password.length >= MIN_PASSWORD

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await manager.register({
        ...(registrationMode === 'invite' ? { invite_code: invite.trim() } : {}),
        email: email.trim(),
        password,
        display_name: displayName.trim(),
      })
      void navigate('/', { replace: true })
    } catch (e) {
      setError(describeProblem(e))
      setSubmitting(false)
    }
  }

  return (
    <AuthLayout title="Create your account">
      {error && (
        <Alert variant="error" className="mb-4">
          {error.message}
        </Alert>
      )}
      <form onSubmit={onSubmit} className="space-y-4">
        {registrationMode === 'invite' && (
          <Field
            label="Invite code"
            name="invite_code"
            autoComplete="off"
            required
            minLength={16}
            maxLength={64}
            spellCheck={false}
            inputClassName="font-mono text-body-sm"
            value={invite}
            onChange={(e) => setInvite(e.target.value)}
            error={error?.fields.invite_code}
          />
        )}
        <Field
          label="Display name"
          name="display_name"
          autoComplete="name"
          required
          maxLength={60}
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          error={error?.fields.display_name}
        />
        <Field
          label="Email"
          type="email"
          name="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          error={error?.fields.email}
        />
        <Field
          label="Password"
          type="password"
          name="password"
          autoComplete="new-password"
          required
          maxLength={128}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          error={error?.fields.password}
          hint={
            <span className="flex items-center justify-between gap-2">
              <span
                className={cn('inline-flex items-center gap-1', longEnough && 'text-success-fg')}
              >
                <Check className={cn('size-3', !longEnough && 'opacity-30')} aria-hidden />
                At least {MIN_PASSWORD} characters
              </span>
              {strength.label && <span aria-live="polite">{strength.label}</span>}
            </span>
          }
        />
        <Button type="submit" className="w-full" loading={submitting}>
          Create account
        </Button>
      </form>
      <p className="mt-6 text-center text-body-sm text-fg-muted">
        Already have an account?{' '}
        <Link to="/login" className="text-primary underline underline-offset-2">
          Sign in
        </Link>
      </p>
    </AuthLayout>
  )
}
