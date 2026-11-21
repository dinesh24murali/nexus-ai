import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it } from 'vitest'
import { AuthManager } from '../lib/auth'
import { AuthProvider } from '../lib/AuthProvider'
import { anonymous, authBody, json, problem, renderApp, signedIn } from '../test-utils'
import { RegisterPage } from './RegisterPage'

beforeEach(() => {
  document.documentElement.dataset.theme = 'light'
})

describe('route guards', () => {
  it('sends a visitor without a session from / to the sign-in page', async () => {
    renderApp('/', anonymous())
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
  })

  it('signs in silently from the refresh cookie and shows the app', async () => {
    renderApp('/', signedIn())
    expect(await screen.findByRole('heading', { name: /hello, marco/i })).toBeInTheDocument()
    expect(screen.getByRole('complementary', { name: 'Sidebar' })).toBeInTheDocument()
  })

  it('shows a loading state, not the login form, while the session is being restored', async () => {
    let release: (r: Response) => void = () => undefined
    renderApp('/', () => new Promise<Response>((resolve) => (release = resolve)))
    expect(screen.getByRole('status', { name: 'Loading' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Sign in' })).not.toBeInTheDocument()
    release(problem(401, 'UNAUTHORIZED'))
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
  })

  it('keeps signed-in users away from /login and /register', async () => {
    renderApp('/login', signedIn())
    expect(await screen.findByRole('heading', { name: /hello, marco/i })).toBeInTheDocument()
  })

  it('renders a 404 page with a way home', async () => {
    renderApp('/definitely/not/a/page', anonymous())
    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go home' })).toHaveAttribute('href', '/')
  })
})

describe('login page', () => {
  it('signs in and lands on the app', async () => {
    const { user, calls } = renderApp(
      '/login',
      anonymous((call) =>
        call.path.endsWith('/auth/login') ? json(200, authBody()) : problem(500, 'X'),
      ),
    )
    await user.type(await screen.findByLabelText('Email'), '  Marco@Example.com ')
    await user.type(screen.getByLabelText('Password'), 'correct-horse-battery')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('heading', { name: /hello, marco/i })).toBeInTheDocument()
    const login = calls.find((c) => c.path.endsWith('/auth/login'))!
    expect(login.body).toEqual({ email: 'Marco@Example.com', password: 'correct-horse-battery' })
  })

  it('shows the error banner and lets the user retry', async () => {
    const { user } = renderApp(
      '/login',
      anonymous(() => problem(401, 'INVALID_CREDENTIALS')),
    )
    await user.type(await screen.findByLabelText('Email'), 'marco@example.com')
    await user.type(screen.getByLabelText('Password'), 'wrong-password-1')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Email or password is incorrect.')
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled()
  })

  it('disables the button while the request is in flight', async () => {
    let release: (r: Response) => void = () => undefined
    const { user } = renderApp(
      '/login',
      anonymous(() => new Promise<Response>((resolve) => (release = resolve))),
    )
    await user.type(await screen.findByLabelText('Email'), 'marco@example.com')
    await user.type(screen.getByLabelText('Password'), 'correct-horse-battery')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    const button = screen.getByRole('button', { name: 'Sign in' })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('aria-busy', 'true')
    release(problem(401, 'INVALID_CREDENTIALS'))
    await waitFor(() => expect(button).toBeEnabled())
  })

  it('says how long to wait when rate limited', async () => {
    const { user } = renderApp(
      '/login',
      anonymous(() => problem(429, 'RATE_LIMITED', 'x', {}, { 'retry-after': '600' })),
    )
    await user.type(await screen.findByLabelText('Email'), 'marco@example.com')
    await user.type(screen.getByLabelText('Password'), 'whatever-password')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Try again in 10 minutes')
  })

  it('shows a calm connectivity message when the network is down', async () => {
    const { user } = renderApp(
      '/login',
      anonymous(() => {
        throw new TypeError('Failed to fetch')
      }),
    )
    await user.type(await screen.findByLabelText('Email'), 'marco@example.com')
    await user.type(screen.getByLabelText('Password'), 'whatever-password')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the server')
  })

  it('reveals and hides the password on request', async () => {
    const { user } = renderApp('/login', anonymous())
    const input = await screen.findByLabelText('Password')
    expect(input).toHaveAttribute('type', 'password')
    await user.click(screen.getByRole('button', { name: 'Show password' }))
    expect(input).toHaveAttribute('type', 'text')
    await user.click(screen.getByRole('button', { name: 'Hide password' }))
    expect(input).toHaveAttribute('type', 'password')
  })

  it('links to registration', async () => {
    renderApp('/login', anonymous())
    expect(await screen.findByRole('link', { name: 'Create an account' })).toHaveAttribute(
      'href',
      '/register',
    )
  })
})

describe('sign out and expired sessions', () => {
  it('signs out, calls the API, and returns to the sign-in page', async () => {
    const { user, calls } = renderApp('/', (call) =>
      call.path.endsWith('/auth/logout') ? new Response(null, { status: 204 }) : signedIn()(call),
    )
    await user.click(await screen.findByRole('button', { name: 'Sign out' }))
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(calls.some((c) => c.path.endsWith('/auth/logout') && c.method === 'POST')).toBe(true)
    // And the session is really gone: visiting a protected page bounces to login.
    expect(screen.queryByText(/hello, marco/i)).not.toBeInTheDocument()
  })

  it('tells the user their session expired (and sends them to sign in) when refresh is rejected', async () => {
    let refreshes = 0
    const { manager } = renderApp('/', (call) => {
      if (call.path.endsWith('/auth/refresh')) {
        refreshes += 1
        return refreshes === 1 ? json(200, authBody()) : problem(401, 'UNAUTHORIZED')
      }
      return problem(401, 'TOKEN_EXPIRED')
    })
    await screen.findByRole('heading', { name: /hello, marco/i })

    await manager.fetch('/api/v1/me') // expired access token; the refresh token is now rejected

    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(
      'Your session expired. Please sign in again.',
    )
  })
})

describe('register page', () => {
  it('prefills the invite code from ?invite=', async () => {
    renderApp('/register?invite=nx_abcdef0123456789abcdef', anonymous())
    expect(await screen.findByLabelText('Invite code')).toHaveValue('nx_abcdef0123456789abcdef')
  })

  it('registers and lands in the app, sending the exact API body', async () => {
    const { user, calls } = renderApp(
      '/register?invite=nx_abcdef0123456789abcdef',
      anonymous((call) =>
        call.path.endsWith('/auth/register') ? json(201, authBody()) : problem(500, 'X'),
      ),
    )
    await user.type(await screen.findByLabelText('Display name'), ' Marco ')
    await user.type(screen.getByLabelText('Email'), 'marco@example.com')
    await user.type(screen.getByLabelText('Password'), 'correct-horse-battery')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByRole('heading', { name: /hello, marco/i })).toBeInTheDocument()
    expect(calls.find((c) => c.path.endsWith('/auth/register'))!.body).toEqual({
      invite_code: 'nx_abcdef0123456789abcdef',
      email: 'marco@example.com',
      password: 'correct-horse-battery',
      display_name: 'Marco',
    })
  })

  it.each([
    ['INVITE_INVALID', 403, 'This invite code is invalid, used or expired.'],
    ['EMAIL_TAKEN', 409, 'An account with this email already exists.'],
  ])('shows %s clearly', async (code, status, text) => {
    const { user } = renderApp(
      '/register?invite=nx_abcdef0123456789abcdef',
      anonymous(() => problem(status, code)),
    )
    await user.type(await screen.findByLabelText('Display name'), 'Marco')
    await user.type(screen.getByLabelText('Email'), 'marco@example.com')
    await user.type(screen.getByLabelText('Password'), 'correct-horse-battery')
    await user.click(screen.getByRole('button', { name: 'Create account' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(text)
  })

  it('attaches server validation errors to the right field', async () => {
    const { user } = renderApp(
      '/register?invite=nx_abcdef0123456789abcdef',
      anonymous(() =>
        problem(422, 'VALIDATION_FAILED', 'x', {
          details: {
            fields: [
              { loc: ['body', 'email'], msg: 'value is not a valid email address', type: 'x' },
            ],
          },
        }),
      ),
    )
    await user.type(await screen.findByLabelText('Display name'), 'Marco')
    await user.type(screen.getByLabelText('Email'), 'marco@example.com')
    await user.type(screen.getByLabelText('Password'), 'correct-horse-battery')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    const email = await screen.findByLabelText('Email')
    expect(email).toHaveAttribute('aria-invalid', 'true')
    expect(email).toHaveAccessibleDescription('value is not a valid email address')
  })

  it('gives live password guidance', async () => {
    const { user } = renderApp('/register', anonymous())
    const password = await screen.findByLabelText('Password')
    expect(screen.getByText('At least 10 characters')).toBeInTheDocument()
    await user.type(password, 'short')
    expect(screen.getByText('Too short')).toBeInTheDocument()
    await user.clear(password)
    await user.type(password, 'correct-horse-battery')
    expect(screen.getByText('Strong')).toBeInTheDocument()
  })

  it('omits the invite field, and the invite_code, when registration is open', async () => {
    const calls: unknown[] = []
    const manager = new AuthManager({
      fetch: (async (input: RequestInfo | URL, init?: RequestInit) => {
        if (input.toString().endsWith('/auth/register')) {
          calls.push(JSON.parse(init!.body as string))
          return json(201, authBody())
        }
        return problem(401, 'UNAUTHORIZED')
      }) as typeof fetch,
      locks: null,
    })
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/register']}>
        <AuthProvider manager={manager}>
          <RegisterPage registrationMode="open" />
        </AuthProvider>
      </MemoryRouter>,
    )
    await screen.findByLabelText('Display name')
    expect(screen.queryByLabelText('Invite code')).not.toBeInTheDocument()
    await user.type(screen.getByLabelText('Display name'), 'Marco')
    await user.type(screen.getByLabelText('Email'), 'marco@example.com')
    await user.type(screen.getByLabelText('Password'), 'correct-horse-battery')
    await user.click(screen.getByRole('button', { name: 'Create account' }))
    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0]).not.toHaveProperty('invite_code')
  })
})
