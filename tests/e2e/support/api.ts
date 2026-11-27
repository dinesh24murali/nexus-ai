import type { APIRequestContext } from '@playwright/test'
import { PASSWORD, createInvite, uniqueEmail } from './invite'

export type Account = { email: string; password: string; token: string }

/** Register a fresh user straight through the API (no UI), as a test fixture. */
export async function registerViaApi(
  request: APIRequestContext,
  name = 'Fixture',
): Promise<Account> {
  const email = uniqueEmail('fx')
  const res = await request.post('/api/v1/auth/register', {
    data: {
      email,
      password: PASSWORD,
      display_name: name,
      invite_code: createInvite(),
    },
  })
  if (!res.ok()) throw new Error(`register failed: ${res.status()} ${await res.text()}`)
  const body = (await res.json()) as { access_token: string }
  return { email, password: PASSWORD, token: body.access_token }
}

/** Create conversations through the API; the last title in `titles` ends up newest. */
export async function seedConversations(
  request: APIRequestContext,
  account: Account,
  titles: string[],
): Promise<string[]> {
  const ids: string[] = []
  for (const title of titles) {
    const res = await request.post('/api/v1/conversations', {
      headers: { authorization: `Bearer ${account.token}` },
      data: { title },
    })
    if (!res.ok()) throw new Error(`create failed: ${res.status()} ${await res.text()}`)
    ids.push(((await res.json()) as { id: string }).id)
  }
  return ids
}
