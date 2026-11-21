import { AlertCircle, CheckCircle2, Loader2, Trash2, UploadCloud } from 'lucide-react'
import { useState, type DragEvent } from 'react'
import { Alert } from '../../components/ui/Alert'
import { Button } from '../../components/ui/Button'
import { cn } from '../../lib/cn'
import { describeProblem } from '../../lib/messages'
import { useDeleteFile, useFiles, type FileObject } from './api'
import { FilePicker } from './AttachButton'
import { describeFileProblem, formatBytes, MAX_AT_ONCE, MAX_FILES, MAX_UPLOAD_MB } from './messages'
import { UploadChips } from './UploadChips'
import { useAttachments } from './uploads'

function Status({ file }: { file: FileObject }) {
  if (file.status === 'ready') {
    return (
      <span className="inline-flex items-center gap-1.5 text-success-fg">
        <CheckCircle2 className="size-4" aria-hidden />
        Ready
      </span>
    )
  }
  if (file.status === 'failed') {
    return (
      <span className="inline-flex items-start gap-1.5 text-error-fg">
        <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
        <span>
          Failed
          {file.error && <span className="block text-caption">{file.error}</span>}
        </span>
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1.5 text-fg-muted">
      <Loader2 className="size-4 animate-spin" aria-hidden />
      Processing…
    </span>
  )
}

function FileRow({ file }: { file: FileObject }) {
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const remove = useDeleteFile()

  async function confirm() {
    setError(null)
    try {
      await remove.mutateAsync(file.id)
    } catch (e) {
      setError(describeFileProblem(e))
    }
  }

  return (
    <tr className="border-t border-border align-top">
      <td className="max-w-64 truncate py-3 pr-4 font-medium" title={file.filename}>
        {file.filename}
      </td>
      <td className="py-3 pr-4 text-fg-muted">{formatBytes(file.size_bytes)}</td>
      <td className="py-3 pr-4">
        <Status file={file} />
      </td>
      <td className="py-3 pr-4 text-fg-muted">{file.chunk_count ?? '—'}</td>
      <td className="py-3 pr-4 whitespace-nowrap text-fg-muted">
        {new Date(file.created_at).toLocaleDateString()}
      </td>
      <td className="py-3 text-right">
        {confirming ? (
          <div className="flex flex-col items-end gap-1">
            <p className="text-caption">Delete this file? It will no longer be searched.</p>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  setConfirming(false)
                  setError(null)
                }}
                disabled={remove.isPending}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                onClick={confirm}
                loading={remove.isPending}
                aria-label={`Confirm delete ${file.filename}`}
              >
                Delete
              </Button>
            </div>
            {error && (
              <p role="alert" className="text-caption text-error-fg">
                {error}
              </p>
            )}
          </div>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            aria-label={`Delete ${file.filename}`}
            onClick={() => setConfirming(true)}
          >
            <Trash2 className="size-4" aria-hidden />
          </Button>
        )}
      </td>
    </tr>
  )
}

/** `/files`: upload, see what the assistant can search, delete (functional spec: Manage files). */
export function FilesPage() {
  const files = useFiles()
  const attachments = useAttachments()
  const [dragging, setDragging] = useState(false)
  const count = files.data?.length ?? 0

  function onDrop(event: DragEvent) {
    event.preventDefault()
    setDragging(false)
    attachments.upload(Array.from(event.dataTransfer.files))
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-8">
      <header className="flex items-baseline justify-between gap-4">
        <h1 className="text-h2">Files</h1>
        {files.data && (
          <p className="text-body-sm text-fg-muted" aria-label="Files used">
            {count}/{MAX_FILES} files
          </p>
        )}
      </header>
      <p className="mt-1 text-body text-fg-muted">
        Files you upload here are searched when you ask about them in a chat. They stay private to
        you.
      </p>

      <div
        onDragOver={(event) => {
          event.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          'mt-6 flex flex-col items-center gap-3 rounded-lg border border-dashed border-border px-6 py-8 text-center',
          dragging && 'border-primary bg-primary/5',
        )}
      >
        <UploadCloud className="size-6 text-fg-muted" aria-hidden />
        <p className="text-body">Drop files here, or choose them</p>
        <FilePicker onPick={attachments.upload} variant="secondary">
          Upload files
        </FilePicker>
        <p className="text-caption text-fg-muted">
          PDF, DOCX, TXT or MD, up to {MAX_UPLOAD_MB} MB each, {MAX_AT_ONCE} at a time.
        </p>
      </div>

      <div className="mt-4">
        <UploadChips attachments={attachments} onlyInFlight />
      </div>

      <section aria-label="Your files" className="mt-6">
        {files.isPending ? (
          <div role="status" aria-label="Loading files" className="py-8" />
        ) : files.isError ? (
          <Alert variant="error">
            <p>{describeProblem(files.error).message}</p>
            <Button size="sm" variant="secondary" className="mt-2" onClick={() => files.refetch()}>
              Try again
            </Button>
          </Alert>
        ) : count === 0 ? (
          <p className="py-8 text-center text-fg-muted">No files yet.</p>
        ) : (
          <table className="w-full text-left text-body-sm">
            <thead>
              <tr className="text-caption text-fg-muted">
                <th scope="col" className="pb-2 pr-4 font-medium">
                  Name
                </th>
                <th scope="col" className="pb-2 pr-4 font-medium">
                  Size
                </th>
                <th scope="col" className="pb-2 pr-4 font-medium">
                  Status
                </th>
                <th scope="col" className="pb-2 pr-4 font-medium">
                  Chunks
                </th>
                <th scope="col" className="pb-2 pr-4 font-medium">
                  Uploaded
                </th>
                <th scope="col" className="pb-2 text-right font-medium">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {files.data.map((file) => (
                <FileRow key={file.id} file={file} />
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
