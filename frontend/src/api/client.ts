import createClient from 'openapi-fetch'
import { useMemo } from 'react'
import { useAuthManager } from '../lib/auth-context'
import { problemFromBody, type AuthManager } from '../lib/auth'
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
        body: request.body === null ? undefined : await bodyOf(request),
      }),
  })
}

/**
 * The request body in a form `AuthManager.fetch` can send again after a token refresh. Text is
 * right for JSON, but a multipart upload is binary and `text()` would corrupt it (the boundary
 * header is already on the request, so the bytes are sent as they are).
 */
async function bodyOf(request: Request): Promise<string | ArrayBuffer> {
  const multipart = request.headers.get('content-type')?.startsWith('multipart/form-data')
  return multipart ? request.arrayBuffer() : request.text()
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
  if (!response.ok || error !== undefined) throw problemFromBody(response, error)
  return data as T
}

/** Like `unwrap` for a call with no response body (204): resolves when it succeeded. */
export async function unwrapEmpty(
  call: Promise<{ error?: unknown; response: Response }>,
): Promise<void> {
  const { error, response } = await call
  if (!response.ok || error !== undefined) throw problemFromBody(response, error)
}
