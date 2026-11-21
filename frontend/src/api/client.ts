import createClient from 'openapi-fetch'
import { useMemo } from 'react'
import { useAuthManager } from '../lib/auth-context'
import { parseProblem, type AuthManager } from '../lib/auth'
import type { paths } from './schema'

/**
 * Typed API client generated from the OpenAPI spec (`src/api/schema.ts`).
 *
 * openapi-fetch hands `fetch` a `Request`; `AuthManager.fetch` takes `(url, init)`. This adapter
 * bridges the two so every call still gets the bearer token and the one refresh-and-retry.
 */
export function createApiClient(manager: AuthManager) {
  return createClient<paths>({
    baseUrl: `${globalThis.location.origin}/api/v1`,
    fetch: async (request) =>
      manager.fetch(request.url, {
        method: request.method,
        headers: request.headers,
        body: request.body === null ? undefined : await request.text(),
      }),
  })
}

export type ApiClient = ReturnType<typeof createApiClient>

export function useApiClient(): ApiClient {
  const manager = useAuthManager()
  return useMemo(() => createApiClient(manager), [manager])
}

/** Unwrap an openapi-fetch result: return the data or throw the API's error envelope. */
export async function unwrap<T>(
  call: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await call
  if (!response.ok || error !== undefined) throw await parseProblem(response)
  return data as T
}
