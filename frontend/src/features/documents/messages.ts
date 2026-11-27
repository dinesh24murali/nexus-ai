import { ApiProblem } from '../../lib/auth'
import { describeProblem } from '../../lib/messages'
import type { DocumentObject } from './api'

/** What a document contains, in words (the `kind` of the API). */
export const KIND_LABEL: Record<DocumentObject['kind'], string> = {
  thread_summary: 'Conversation summary',
  answer: 'Answer as a PDF',
  news_brief: 'News brief',
  research_report: 'Research report',
}

/** `3 pages · 84 KB`, once the PDF exists. */
export function describeFile(doc: DocumentObject): string | null {
  if (doc.status !== 'ready') return null
  const parts: string[] = []
  if (doc.page_count != null)
    parts.push(`${doc.page_count} ${doc.page_count === 1 ? 'page' : 'pages'}`)
  if (doc.size_bytes != null) parts.push(formatSize(doc.size_bytes))
  return parts.length > 0 ? parts.join(' · ') : null
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** Friendly text for a failed download or delete. */
export function describeDocumentProblem(error: unknown): string {
  if (error instanceof ApiProblem) {
    if (error.code === 'NOT_FOUND') return 'This document no longer exists.'
    if (error.code === 'DOCUMENT_NOT_READY') return 'The PDF is not ready yet.'
  }
  return describeProblem(error).message
}

/**
 * A file name for the saved PDF. The title is text written by a model, so only the characters a
 * file name can safely hold are kept (the server sends the same kind of name in its header).
 */
export function fileNameFor(title: string): string {
  const plain = title
    .normalize('NFKD')
    .replace(/[^\x20-\x7e]/g, '')
    .replace(/[^A-Za-z0-9 ._-]+/g, ' ')
    .replace(/\.{2,}/g, ' ') // no `..` in a name, even though a slash can no longer be there
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 80)
    .replace(/^[ ._-]+|[ ._-]+$/g, '')
  return `${plain || 'document'}.pdf`
}
