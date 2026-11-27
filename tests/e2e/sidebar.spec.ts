import { expect, test, type Page } from '@playwright/test'
import { PASSWORD, createInvite, uniqueEmail } from './support/invite'
import { registerViaApi, seedConversations } from './support/api'

/** Registering through `page.request` leaves the refresh cookie in this browser context, so the
 *  app signs in silently, exactly like a returning visitor. */
async function openApp(page: Page) {
  await page.goto('/')
  await expect(page.getByRole('complementary', { name: 'Sidebar' })).toBeVisible()
}

const sidebarLinks = (page: Page) =>
  page.getByRole('list', { name: 'Conversations' }).getByRole('link')

async function openMenu(page: Page, title: string) {
  const item = page.getByRole('link', { name: title, exact: true })
  await item.hover()
  await page.getByRole('button', { name: `Actions for ${title}` }).click()
}

test('create, rename and delete a conversation from the sidebar (and it survives a reload)', async ({
  page,
}) => {
  const account = await registerViaApi(page.request)
  await openApp(page)
  await expect(page.getByText('No conversations yet.')).toBeVisible()

  // Create: "New chat" makes a conversation and opens it.
  await page.getByRole('button', { name: 'New chat' }).click()
  await expect(page).toHaveURL(/\/c\/[0-9a-f-]{36}$/)
  await expect(page.getByRole('heading', { level: 1, name: 'New chat' })).toBeVisible()
  await expect(sidebarLinks(page)).toHaveText(['New chat'])
  await expect(sidebarLinks(page).first()).toHaveAttribute('aria-current', 'page')

  // Rename: shown in the sidebar and the page heading, and still there after a reload.
  await openMenu(page, 'New chat')
  await page.getByRole('menuitem', { name: 'Rename' }).click()
  const input = page.getByRole('textbox', { name: 'Conversation title' })
  await input.fill('Q3 churn analysis')
  await input.press('Enter')
  await expect(sidebarLinks(page)).toHaveText(['Q3 churn analysis'])
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Q3 churn analysis')
  await page.reload()
  await expect(sidebarLinks(page)).toHaveText(['Q3 churn analysis'])

  // Delete needs a confirmation; cancelling keeps it.
  await openMenu(page, 'Q3 churn analysis')
  await page.getByRole('menuitem', { name: 'Delete' }).click()
  const confirm = page.getByRole('group', { name: 'Confirm delete' })
  await confirm.getByRole('button', { name: 'Cancel' }).click()
  await expect(sidebarLinks(page)).toHaveText(['Q3 churn analysis'])

  await openMenu(page, 'Q3 churn analysis')
  await page.getByRole('menuitem', { name: 'Delete' }).click()
  await page
    .getByRole('group', { name: 'Confirm delete' })
    .getByRole('button', { name: 'Delete' })
    .click()

  // Deleting the open conversation goes home; the conversation is gone for good.
  await expect(page).toHaveURL('/')
  await expect(page.getByText('No conversations yet.')).toBeVisible()
  await page.reload()
  await expect(page.getByText('No conversations yet.')).toBeVisible()
})

test('the list is newest-first and loads further pages on scroll', async ({ page }) => {
  const account = await registerViaApi(page.request)
  const titles = Array.from({ length: 35 }, (_, i) => `Chat ${String(i + 1).padStart(2, '0')}`)
  await seedConversations(page.request, account, titles)
  await openApp(page)

  // First page: 30 newest.
  await expect(sidebarLinks(page)).toHaveCount(30)
  await expect(sidebarLinks(page).first()).toHaveText('Chat 35')

  // Scrolling the list to the bottom loads the rest, in order, without duplicates.
  await page.getByRole('navigation', { name: 'Conversation history' }).evaluate((el) => {
    el.scrollTop = el.scrollHeight
  })
  await expect(sidebarLinks(page)).toHaveCount(35)
  const all = await sidebarLinks(page).allTextContents()
  expect(all).toEqual([...titles].reverse())
  await expect(page.getByRole('button', { name: 'Load more' })).toHaveCount(0)
})

test("another user's conversation is not listed, and its URL looks like a missing page", async ({
  page,
  browser,
}) => {
  const alice = await registerViaApi(page.request, 'Alice')
  const [aliceChat] = await seedConversations(page.request, alice, ['Alice private plans'])

  // Bob signs up through the UI path and tries Alice's conversation URL.
  const bobContext = await browser.newContext({
    baseURL: test.info().project.use.baseURL,
  })
  const bobPage = await bobContext.newPage()
  const bob = uniqueEmail('bob')
  await bobPage.goto(`/register?invite=${createInvite()}`)
  await bobPage.getByLabel('Display name', { exact: true }).fill('Bob')
  await bobPage.getByLabel('Email', { exact: true }).fill(bob)
  await bobPage.getByLabel('Password', { exact: true }).fill(PASSWORD)
  await bobPage.getByRole('button', { name: 'Create account' }).click()
  await expect(bobPage.getByText('No conversations yet.')).toBeVisible()

  await bobPage.goto(`/c/${aliceChat}`)
  await expect(bobPage.getByRole('heading', { name: 'Conversation not found' })).toBeVisible()
  await expect(bobPage.getByText('Alice private plans')).toHaveCount(0)
  await bobContext.close()
})
