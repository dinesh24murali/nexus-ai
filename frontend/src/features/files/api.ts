import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { unwrap, unwrapEmpty, useApiClient } from '../../api/client'
import type { components } from '../../api/schema'
import { useAuth } from '../../lib/auth-context'

export type FileObject = components['schemas']['FileObject']
type FilePage = components['schemas']['FilePage']

/** The API caps a user at 50 files and a page at 100, so one request always holds them all. */
const LIST_LIMIT = 100
export const POLL_MS = 2000

/** Keys carry the user id, like every other query (frontend/AGENTS.md, State Management). */
export function useFileKeys() {
  const { user } = useAuth()
  const uid = user?.id ?? 'anonymous'
  return { list: ['files', uid, 'list'] as const }
}

export const isFinal = (file: FileObject) => file.status === 'ready' || file.status === 'failed'

/** The caller's files, newest first. Re-read every 2 s while any file is still being processed. */
export function useFiles({ enabled = true }: { enabled?: boolean } = {}) {
  const api = useApiClient()
  const keys = useFileKeys()
  return useQuery({
    queryKey: keys.list,
    enabled,
    queryFn: async () =>
      (await unwrap<FilePage>(api.GET('/files', { params: { query: { limit: LIST_LIMIT } } })))
        .items,
    refetchInterval: (query) => (query.state.data?.some((f) => !isFinal(f)) ? POLL_MS : false),
  })
}

function putFirst(qc: QueryClient, key: readonly unknown[], file: FileObject) {
  qc.setQueryData<FileObject[]>(key, (files) => [
    file,
    ...(files ?? []).filter((f) => f.id !== file.id),
  ])
}

export function useUploadFile() {
  const api = useApiClient()
  const keys = useFileKeys()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (file: File) =>
      unwrap<FileObject>(
        api.POST('/files', {
          // The generated type says `file: string`; the real multipart body is built below.
          body: { file: '' },
          bodySerializer: () => {
            const form = new FormData()
            form.append('file', file)
            return form
          },
        }),
      ),
    onSuccess: (created) => {
      putFirst(qc, keys.list, created) // starts the polling while it is processed
      return qc.invalidateQueries({ queryKey: keys.list })
    },
  })
}

export function useDeleteFile() {
  const api = useApiClient()
  const keys = useFileKeys()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      unwrapEmpty(api.DELETE('/files/{fileId}', { params: { path: { fileId: id } } })),
    onSuccess: (_void, id) => {
      qc.setQueryData<FileObject[]>(keys.list, (files) => files?.filter((f) => f.id !== id))
      return qc.invalidateQueries({ queryKey: keys.list })
    },
  })
}
