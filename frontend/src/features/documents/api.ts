import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { unwrap, unwrapEmpty, useApiClient } from '../../api/client'
import type { components } from '../../api/schema'
import { ApiProblem, parseProblem } from '../../lib/auth'
import { useAuth, useAuthManager } from '../../lib/auth-context'
import { fileNameFor } from './messages'
import { POLL_MS } from './timing'

export type DocumentObject = components['schemas']['DocumentObject']
type DocumentPage = components['schemas']['DocumentPage']

/** A user keeps at most 100 documents (`DOCUMENTS_KEEP`) and a page holds 100: one request. */
const LIST_LIMIT = 100

/** Keys carry the user id, like every other query (frontend/AGENTS.md, State Management). */
export function useDocumentKeys() {
  const { user } = useAuth()
  const uid = user?.id ?? 'anonymous'
  return {
    list: ['documents', uid, 'list'] as const,
    one: (id: string) => ['documents', uid, 'one', id] as const,
  }
}

export const isSettled = (doc: DocumentObject) => doc.status !== 'pending'

/** The caller's documents, newest first. Re-read every 2 s while any is still being rendered. */
export function useDocuments() {
  const api = useApiClient()
  const keys = useDocumentKeys()
  return useQuery({
    queryKey: keys.list,
    queryFn: async () =>
      (
        await unwrap<DocumentPage>(
          api.GET('/documents', { params: { query: { limit: LIST_LIMIT } } }),
        )
      ).items,
    refetchInterval: (query) => (query.state.data?.some((d) => !isSettled(d)) ? POLL_MS : false),
  })
}

/**
 * One document, for the card under an answer. It starts from what the message carried and is
 * re-read every 2 s until the render has finished. A 404 means it was deleted (the card says so).
 */
export function useDocument(initial: DocumentObject) {
  const api = useApiClient()
  const keys = useDocumentKeys()
  return useQuery({
    queryKey: keys.one(initial.id),
    initialData: initial,
    queryFn: () =>
      unwrap<DocumentObject>(
        api.GET('/documents/{documentId}', { params: { path: { documentId: initial.id } } }),
      ),
    // A 404 is an answer, not a glitch: do not retry it.
    retry: (count, error) => !(error instanceof ApiProblem && error.status === 404) && count < 2,
    refetchInterval: (query) => {
      const doc = query.state.data
      const gone = query.state.error instanceof ApiProblem && query.state.error.status === 404
      return doc && !isSettled(doc) && !gone ? POLL_MS : false
    },
  })
}

export function useDeleteDocument() {
  const api = useApiClient()
  const keys = useDocumentKeys()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      unwrapEmpty(api.DELETE('/documents/{documentId}', { params: { path: { documentId: id } } })),
    onSuccess: (_void, id) => {
      qc.setQueryData<DocumentObject[]>(keys.list, (docs) => docs?.filter((d) => d.id !== id))
      qc.removeQueries({ queryKey: keys.one(id) })
      return qc.invalidateQueries({ queryKey: keys.list })
    },
  })
}

/**
 * Download the PDF: a Bearer fetch (so the token refresh works and no token ever sits in a URL),
 * then the blob is saved through a short-lived object URL and a `download` link.
 */
export function useDownloadDocument() {
  const manager = useAuthManager()
  return useMutation({
    mutationFn: async (doc: Pick<DocumentObject, 'id' | 'title'>) => {
      const response = await manager.fetch(
        `${globalThis.location.origin}/api/v1/documents/${doc.id}/download`,
      )
      if (!response.ok) throw await parseProblem(response)
      const blob = new Blob([await response.arrayBuffer()], { type: 'application/pdf' })
      saveBlob(blob, fileNameFor(doc.title))
    },
  })
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.rel = 'noopener'
  link.hidden = true
  document.body.append(link)
  link.click()
  link.remove()
  // The browser has the bytes once the click has been handled; release the memory a moment later.
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}
