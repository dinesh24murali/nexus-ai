import { AlertCircle, CheckCircle2, Download, Loader2, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { Alert } from '../../components/ui/Alert'
import { Button } from '../../components/ui/Button'
import { describeProblem } from '../../lib/messages'
import { useDeleteDocument, useDocuments, useDownloadDocument, type DocumentObject } from './api'
import { describeDocumentProblem, describeFile, KIND_LABEL } from './messages'

function Status({ doc }: { doc: DocumentObject }) {
  if (doc.status === 'ready') {
    return (
      <span className="inline-flex items-center gap-1.5 text-success-fg">
        <CheckCircle2 className="size-4" aria-hidden />
        Ready
      </span>
    )
  }
  if (doc.status === 'failed') {
    return (
      <span className="inline-flex items-start gap-1.5 text-error-fg">
        <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
        <span>
          Failed
          {doc.error && <span className="block text-caption">{doc.error}</span>}
        </span>
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1.5 text-fg-muted">
      <Loader2 className="size-4 animate-spin" aria-hidden />
      Preparing…
    </span>
  )
}

function DocumentRow({ doc }: { doc: DocumentObject }) {
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const remove = useDeleteDocument()
  const download = useDownloadDocument()

  async function confirm() {
    setError(null)
    try {
      await remove.mutateAsync(doc.id)
    } catch (e) {
      setError(describeDocumentProblem(e))
    }
  }

  return (
    <tr className="border-t border-border align-top">
      <td className="max-w-72 py-3 pr-4">
        <p className="font-medium break-words">{doc.title}</p>
        {doc.conversation_id && (
          <Link
            to={`/c/${doc.conversation_id}`}
            className="text-caption text-primary hover:underline"
            aria-label={`Open the chat of ${doc.title}`}
          >
            Open chat
          </Link>
        )}
      </td>
      <td className="py-3 pr-4 text-fg-muted">{KIND_LABEL[doc.kind]}</td>
      <td className="py-3 pr-4">
        <Status doc={doc} />
        {describeFile(doc) && (
          <span className="block text-caption text-fg-muted">{describeFile(doc)}</span>
        )}
      </td>
      <td className="py-3 pr-4 whitespace-nowrap text-fg-muted">
        {new Date(doc.created_at).toLocaleDateString()}
      </td>
      <td className="py-3 text-right">
        {confirming ? (
          <div className="flex flex-col items-end gap-1">
            <p className="text-caption">Delete this document? The PDF will be removed.</p>
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
                aria-label={`Confirm delete ${doc.title}`}
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
          <div className="flex flex-col items-end gap-1">
            <div className="flex gap-1">
              {doc.status === 'ready' && (
                <Button
                  size="sm"
                  variant="secondary"
                  loading={download.isPending}
                  aria-label={`Download ${doc.title}`}
                  onClick={() => download.mutate(doc)}
                >
                  <Download className="size-4" aria-hidden />
                </Button>
              )}
              <Button
                size="sm"
                variant="ghost"
                aria-label={`Delete ${doc.title}`}
                onClick={() => setConfirming(true)}
              >
                <Trash2 className="size-4" aria-hidden />
              </Button>
            </div>
            {download.isError && (
              <p role="alert" className="text-caption text-error-fg">
                {describeDocumentProblem(download.error)}
              </p>
            )}
          </div>
        )}
      </td>
    </tr>
  )
}

/** `/documents`: the PDFs made in chats: download or delete them (functional spec: Documents). */
export function DocumentsPage() {
  const documents = useDocuments()
  const count = documents.data?.length ?? 0

  return (
    <div className="mx-auto max-w-4xl px-6 py-8">
      <header className="flex items-baseline justify-between gap-4">
        <h1 className="text-h2">Documents</h1>
      </header>
      <p className="mt-1 text-body text-fg-muted">
        PDFs Nexus made for you in a chat. Ask for one with <code className="font-mono">/pdf</code>{' '}
        or <code className="font-mono">/brief &lt;topic&gt;</code>. They stay private to you.
      </p>

      <section aria-label="Your documents" className="mt-6">
        {documents.isPending ? (
          <div role="status" aria-label="Loading documents" className="py-8" />
        ) : documents.isError ? (
          <Alert variant="error">
            <p>{describeProblem(documents.error).message}</p>
            <Button
              size="sm"
              variant="secondary"
              className="mt-2"
              onClick={() => documents.refetch()}
            >
              Try again
            </Button>
          </Alert>
        ) : count === 0 ? (
          <p className="py-8 text-center text-fg-muted">No documents yet.</p>
        ) : (
          <table className="w-full text-left text-body-sm">
            <thead>
              <tr className="text-caption text-fg-muted">
                <th scope="col" className="pb-2 pr-4 font-medium">
                  Title
                </th>
                <th scope="col" className="pb-2 pr-4 font-medium">
                  Type
                </th>
                <th scope="col" className="pb-2 pr-4 font-medium">
                  Status
                </th>
                <th scope="col" className="pb-2 pr-4 font-medium">
                  Created
                </th>
                <th scope="col" className="pb-2 text-right font-medium">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {documents.data.map((doc) => (
                <DocumentRow key={doc.id} doc={doc} />
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
