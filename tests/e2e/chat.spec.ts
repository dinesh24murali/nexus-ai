import { expect, test, type Page } from '@playwright/test'
import { registerViaApi } from './support/api'

/**
 * Chat against the real stack. The API and worker must run with LLM_MODE=fake and a small
 * LLM_FAKE_TOKEN_DELAY_S (docs/02_outputs/06_dev_setup.md); the scripted model answers
 * "Fake reply: <your message>" word by word, and `[[fixture:long]]` gives a long answer to stop.
 */

async function openNewChat(page: Page) {
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1, name: /^Hello,/ })).toBeVisible()
}

const box = (page: Page) => page.getByRole('textbox', { name: 'Message' })
const replies = (page: Page) => page.getByRole('article', { name: 'Nexus reply' })

/** The fake model must be active: a real-mode stack has no API key and every answer would fail. */
test.beforeAll(async ({ request, baseURL }) => {
  const account = await registerViaApi(request, 'Probe')
  const auth = { authorization: `Bearer ${account.token}` }
  const created = await request.post(`${baseURL}/api/v1/conversations`, { headers: auth, data: {} })
  const { id } = (await created.json()) as { id: string }
  const sent = await request.post(`${baseURL}/api/v1/conversations/${id}/messages`, {
    headers: auth,
    data: { content: 'probe' },
  })
  const text = (await sent.text())
    .split('\n')
    .filter((line) => line.startsWith('data: '))
    .map((line) => JSON.parse(line.slice(6)) as { type: string; delta?: string })
    .map((event) => (event.type === 'token' ? event.delta : ''))
    .join('')
  if (text !== 'Fake reply: probe') {
    throw new Error(
      'The stack is not running the fake LLM. Start it with LLM_MODE=fake (and ' +
        'LLM_FAKE_TOKEN_DELAY_S=0.05) before running the chat e2e tests.',
    )
  }
})

test('send a message from the home page: it streams, is saved, and gets a title', async ({
  page,
}) => {
  await registerViaApi(page.request)
  const problems: string[] = []
  page.on('console', (m) => m.type() === 'error' && problems.push(m.text()))
  page.on('pageerror', (e) => problems.push(String(e)))
  await openNewChat(page)

  await box(page).fill('Hello Nexus')
  await box(page).press('Enter')

  await expect(page).toHaveURL(/\/c\/[0-9a-f-]{36}$/)
  await expect(page.getByRole('article', { name: 'Your message' })).toHaveText('Hello Nexus')
  await expect(replies(page)).toContainText('Fake reply: Hello Nexus')
  await expect(page.getByRole('button', { name: 'Send' })).toBeVisible() // the run is over
  // The worker names the conversation after the first answer; the sidebar follows without a reload.
  await expect(
    page.getByRole('list', { name: 'Conversations' }).getByRole('link', { name: 'Fake title' }),
  ).toBeVisible({ timeout: 20_000 })

  // Everything is stored: a reload shows the same conversation.
  await page.reload()
  await expect(page.getByRole('article', { name: 'Your message' })).toHaveText('Hello Nexus')
  await expect(replies(page)).toContainText('Fake reply: Hello Nexus')
  expect(problems, 'console errors (a CSP violation shows up here)').toEqual([])
})

test('Stop cancels the run and keeps the partial answer', async ({ page }) => {
  await registerViaApi(page.request)
  await openNewChat(page)

  await box(page).fill('[[fixture:long]] go')
  await box(page).press('Enter')
  await expect(replies(page)).toContainText('This is a long scripted answer')
  await page.getByRole('button', { name: 'Stop' }).click()

  await expect(page.getByText('Stopped')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Send' })).toBeVisible()
  const partial = (await replies(page).textContent()) ?? ''
  expect(partial).toContain('This is a long scripted answer')
  expect(partial).not.toContain('The end.') // it really was cut short

  // The partial answer was saved, and the conversation takes the next message.
  await page.reload()
  await expect(page.getByText('Stopped')).toBeVisible()
  await box(page).fill('and now?')
  await box(page).press('Enter')
  await expect(replies(page).last()).toContainText('Fake reply: and now?')
})

test('Regenerate replaces the latest answer with a new one', async ({ page }) => {
  await registerViaApi(page.request)
  await openNewChat(page)
  await box(page).fill('first question')
  await box(page).press('Enter')
  await expect(replies(page)).toContainText('Fake reply: first question')
  await expect(page.getByRole('button', { name: 'Regenerate' })).toBeVisible()

  const regenerated = page.waitForRequest(
    (r) => r.method() === 'POST' && r.url().endsWith('/regenerate'),
  )
  await page.getByRole('button', { name: 'Regenerate' }).click()
  await regenerated

  // Still exactly one answer: the new one replaced the old one rather than being added.
  await expect(page.getByRole('button', { name: 'Regenerate' })).toBeVisible()
  await expect(replies(page)).toHaveCount(1)
  await expect(replies(page)).toContainText('Fake reply: first question')
  await page.reload()
  await expect(replies(page)).toHaveCount(1)
})

test('a message the moderation refuses is not sent and stays in the box', async ({ page }) => {
  await registerViaApi(page.request)
  await openNewChat(page)
  // On the home page the conversation is created first, then the send is refused.
  await box(page).fill('something [[moderation:flag]] awful')
  await box(page).press('Enter')

  await expect(page.getByRole('alert')).toContainText('may violate the content policy')
  await expect(box(page)).toHaveValue('something [[moderation:flag]] awful')
  await expect(page.getByRole('article', { name: 'Your message' })).toHaveCount(0)

  // A different message works in the same conversation.
  await box(page).fill('a fine message')
  await box(page).press('Enter')
  await expect(replies(page)).toContainText('Fake reply: a fine message')
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('code in an answer is shown as a highlighted block with a copy button, and HTML is inert', async ({
  page,
  context,
}) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  await registerViaApi(page.request)
  const problems: string[] = []
  page.on('console', (m) => m.type() === 'error' && problems.push(m.text()))
  page.on('pageerror', (e) => problems.push(String(e)))
  await openNewChat(page)

  // The fake model echoes the message, so this is the "answer" the page has to render safely.
  await box(page).fill(
    'Look:\n```python\ndef f():\n    return 1\n```\n<img src=x onerror="document.title=\'pwned\'"> **bold**',
  )
  await box(page).press('Enter')

  const reply = replies(page)
  await expect(reply.getByRole('button', { name: 'Copy code' })).toBeVisible()
  await expect(reply.locator('pre code span.text-accent', { hasText: 'def' })).toBeVisible() // Shiki
  await expect(reply.locator('strong')).toHaveText('bold')
  await expect(reply.locator('img')).toHaveCount(0)
  await expect(reply).toContainText('<img src=x')
  expect(await page.title()).not.toBe('pwned')
  expect(await reply.locator('[style]').count()).toBe(0)

  await reply.getByRole('button', { name: 'Copy code' }).click()
  await expect(reply.getByRole('button', { name: 'Copied' })).toBeVisible()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('def f():\n    return 1')
  expect(problems, 'console errors (a CSP violation shows up here)').toEqual([])
})

test('the sidebar shows today\'s spend against the daily budget', async ({ page }) => {
  await registerViaApi(page.request)
  await openNewChat(page)
  const meter = page.getByRole('region', { name: 'Usage today' })
  await expect(meter).toContainText(/\$\d+\.\d\d of \$\d+\.\d\d/)
  await expect(meter.getByRole('progressbar', { name: 'Daily spend' })).toBeVisible()
})
