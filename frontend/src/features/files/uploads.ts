import { useCallback, useRef, useState } from 'react'
import { useUploadFile } from './api'
import { describeFileProblem, MAX_AT_ONCE } from './messages'

/** One file this tab is uploading, or has uploaded. What happens after the upload (processing,
 * ready, failed) is read from the polled files list, not tracked here. */
export type Attachment =
  | { key: number; name: string; state: 'uploading' }
  | { key: number; name: string; state: 'error'; message: string }
  | { key: number; name: string; state: 'uploaded'; fileId: string }

export type Attachments = ReturnType<typeof useAttachments>

/**
 * Uploads chosen files (at most 5 at once, functional spec FS-004) and keeps a short list of them
 * for the screen. The server is the judge of type, size, duplicates and the per-user limit; its
 * refusal text is shown on the file it concerns.
 *
 * Progress is a state (uploading, then the server's own status), not a percentage: `fetch`
 * cannot report upload bytes, and every API call must go through the session manager.
 */
export function useAttachments() {
  const send = useUploadFile().mutateAsync
  const [items, setItems] = useState<Attachment[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const nextKey = useRef(0)

  const patch = useCallback((key: number, next: Attachment) => {
    setItems((current) => current.map((item) => (item.key === key ? next : item)))
  }, [])

  const upload = useCallback(
    (chosen: File[]) => {
      const files = chosen.slice(0, MAX_AT_ONCE)
      setNotice(
        chosen.length > MAX_AT_ONCE
          ? `You can upload up to ${MAX_AT_ONCE} files at once; the others were skipped.`
          : null,
      )
      for (const file of files) {
        const key = nextKey.current++
        setItems((current) => [...current, { key, name: file.name, state: 'uploading' }])
        send(file).then(
          (created) => patch(key, { key, name: file.name, state: 'uploaded', fileId: created.id }),
          (error: unknown) =>
            patch(key, {
              key,
              name: file.name,
              state: 'error',
              message: describeFileProblem(error),
            }),
        )
      }
    },
    [send, patch],
  )

  const dismiss = useCallback(
    (key: number) => setItems((current) => current.filter((item) => item.key !== key)),
    [],
  )

  return { items, notice, upload, dismiss }
}
