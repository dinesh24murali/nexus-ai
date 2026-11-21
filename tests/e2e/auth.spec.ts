import { expect, test, type Page } from '@playwright/test'
import { PASSWORD, createInvite, uniqueEmail } from './support/invite'

/** Console errors that are normal: a visitor without a session gets a 401 from the silent refresh. */
const EXPECTED_NOISE = /Failed to load resource: the server responded with a status of 401/

function watchConsole(page: Page) {
  const problems: string[] = []
  page.on('console', (msg) => {
    if (msg.type() === 'error' && !EXPECTED_NOISE.test(msg.text())) problems.push(msg.text())
  })
  page.on('pageerror', (err) => problems.push(String(err)))
  return problems
}

async function register(page: Page, opts: { invite: string; email: string; name: string }) {
  await page.goto(`/register?invite=${opts.invite}`)
  await page.getByLabel('Display name', { exact: true }).fill(opts.name)
  await page.getByLabel('Email', { exact: true }).fill(opts.email)
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  await page.getByRole('button', { name: 'Create account' }).click()
}

test('invite → register → reload → sign out → sign in', async ({ page }) => {
  const problems = watchConsole(page)
  const email = uniqueEmail()

  // Register with a real invite minted by the admin CLI.
  await register(page, { invite: createInvite(), email, name: 'Ada' })
  await expect(page.getByRole('heading', { name: 'Hello, Ada' })).toBeVisible()
  await expect(page).toHaveURL('/')

  // The session survives a reload (silent refresh from the HttpOnly cookie).
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Hello, Ada' })).toBeVisible()

  // Access token is in memory only: nothing in web storage, and the refresh cookie is not readable.
  const stored = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))
  expect(stored).not.toMatch(/eyJ/) // no JWT anywhere
  expect(await page.evaluate(() => document.cookie)).not.toContain('nexus_refresh')

  // Sign out → login page; protected pages now bounce.
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()

  // Sign back in with the same credentials.
  await page.getByLabel('Email', { exact: true }).fill(email)
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Hello, Ada' })).toBeVisible()

  expect(problems, 'no unexpected console errors').toEqual([])
})

test('a visitor is sent to sign in; wrong credentials are rejected clearly', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()

  await page.getByLabel('Email', { exact: true }).fill(uniqueEmail('nobody'))
  await page.getByLabel('Password', { exact: true }).fill('not-a-real-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('alert')).toHaveText('Email or password is incorrect.')
})

test('an invite works once; the second use is refused with the standard message', async ({ page }) => {
  const invite = createInvite()
  await register(page, { invite, email: uniqueEmail(), name: 'First' })
  await expect(page.getByRole('heading', { name: 'Hello, First' })).toBeVisible()
  await page.getByRole('button', { name: 'Sign out' }).click()

  await register(page, { invite, email: uniqueEmail(), name: 'Second' })
  await expect(page.getByRole('alert')).toHaveText('This invite code is invalid, used or expired.')
  await expect(page.getByRole('heading', { name: 'Create your account' })).toBeVisible()
})

test('the invite code is prefilled from the link', async ({ page }) => {
  await page.goto('/register?invite=nx_0123456789abcdef0123456789abcdef')
  await expect(page.getByLabel('Invite code', { exact: true })).toHaveValue('nx_0123456789abcdef0123456789abcdef')
})

test('unknown routes show the 404 page', async ({ page }) => {
  await page.goto('/no/such/page')
  await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible()
})

test('theme: choice applies immediately, persists across reloads, and follows the OS on System', async ({
  page,
}) => {
  await page.emulateMedia({ colorScheme: 'light' })
  await page.goto('/login')
  const theme = () => page.evaluate(() => document.documentElement.dataset.theme)

  expect(await theme()).toBe('light')
  await page.getByRole('radio', { name: 'Dark' }).click()
  expect(await theme()).toBe('dark')

  await page.reload()
  expect(await theme()).toBe('dark') // set by theme-init.js before first paint
  await expect(page.getByRole('radio', { name: 'Dark' })).toBeChecked()

  await page.getByRole('radio', { name: 'System' }).click()
  expect(await theme()).toBe('light')
  await page.emulateMedia({ colorScheme: 'dark' })
  await expect.poll(theme).toBe('dark')
})

test('security headers are served and the app runs cleanly under the CSP', async ({ page }) => {
  const violations: string[] = []
  page.on('console', (msg) => {
    if (/content security policy/i.test(msg.text())) violations.push(msg.text())
  })

  const response = await page.goto('/login')
  const headers = response!.headers()
  expect(headers['content-security-policy']).toContain("default-src 'self'")
  expect(headers['content-security-policy']).toContain("frame-ancestors 'none'")
  expect(headers['x-content-type-options']).toBe('nosniff')
  expect(headers['referrer-policy']).toBe('no-referrer')

  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  // The self-hosted font must load (CSP font-src falls back to 'self').
  await page.evaluate(() => document.fonts.ready)
  expect(violations).toEqual([])
})

test('every page has a title and focusable, labelled controls', async ({ page }) => {
  await page.goto('/login')
  await expect(page).toHaveTitle('Nexus AI')
  for (const name of ['Email', 'Password']) {
    await expect(page.getByLabel(name, { exact: true })).toBeVisible()
  }
  // Keyboard path: tab reaches the submit button without a mouse.
  await page.getByLabel('Email', { exact: true }).focus()
  await page.keyboard.press('Tab') // password
  await page.keyboard.press('Tab') // show-password toggle
  await page.keyboard.press('Tab') // submit
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeFocused()
})
