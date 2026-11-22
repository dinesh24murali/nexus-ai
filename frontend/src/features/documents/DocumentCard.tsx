import { AlertCircle, Download, FileText, Loader2 } from 'lucide-react'
import { Button } from '../../components/ui/Button'
import { ApiProblem } from '../../lib/auth'
import { useDocument, useDownloadDocument, type DocumentObject } from './api'
import { describeDocumentProblem, describeFile, KIND_LABEL } from './messages'

/**
 * A PDF made in a chat: under the answer that announced it. It shows the render's progress (the
 * worker makes the file after the answer is written), then offers the download. Everything is
 * text: a title is written by a model and never becomes markup.
 */
export function DocumentCard({ document: initial }: { document: DocumentObject }) {
  const query = useDocument(initial)
  const download = useDownloadDocument()
  const doc = query.data
  const gone = query.error instanceof ApiProblem && query.error.status === 404

  return (
    <section
      aria-label={`Document: ${initial.title}`}
      className="mt-3 flex max-w-xl items-start gap-3 rounded-lg border border-border bg-surface p-3"
    >
      <FileText className="mt-0.5 size-5 shrink-0 text-fg-muted" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-label font-medium break-words">{doc.title}</p>
        <p className="text-caption text-fg-muted">{KIND_LABEL[doc.kind]}</p>
        {gone ? (
          <p className="mt-1 text-caption text-fg-muted">This document was deleted.</p>
        ) : doc.status === 'pending' ? (
          <p className="mt-1 inline-flex items-center gap-1.5 text-caption text-fg-muted">
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
            Preparing the PDF…
          </p>
        ) : doc.status === 'failed' ? (
          <p className="mt-1 flex items-start gap-1.5 text-caption text-error-fg">
            <AlertCircle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
            <span>{doc.error ?? 'The PDF could not be made.'}</span>
          </p>
        ) : (
          <p className="mt-1 text-caption text-fg-muted">{describeFile(doc)}</p>
        )}
        {download.isError && (
          <p role="alert" className="mt-1 text-caption text-error-fg">
            {describeDocumentProblem(download.error)}
          </p>
        )}
      </div>
      {!gone && doc.status === 'ready' && (
        <Button
          size="sm"
          variant="secondary"
          loading={download.isPending}
          aria-label={`Download ${doc.title}`}
          onClick={() => download.mutate(doc)}
        >
          <Download className="size-4" aria-hidden />
          Download
        </Button>
      )}
    </section>
  )
}

export function DocumentCards({ documents }: { documents?: DocumentObject[] }) {
  if (!documents || documents.length === 0) return null
  return (
    <div>
      {documents.map((doc) => (
        <DocumentCard key={doc.id} document={doc} />
      ))}
    </div>
  )
}
