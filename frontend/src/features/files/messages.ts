import { ApiProblem } from '../../lib/auth'
import { describeProblem } from '../../lib/messages'

export const MAX_FILES = 50
export const MAX_AT_ONCE = 5
export const MAX_UPLOAD_MB = 20
export const ACCEPT = '.pdf,.docx,.txt,.md'

/** Friendly text for a failed upload or delete (functional spec FS-004). */
export function describeFileProblem(error: unknown): string {
  if (error instanceof ApiProblem) {
    switch (error.code) {
      case 'FILE_TOO_LARGE':
        return `That file is larger than ${MAX_UPLOAD_MB} MB.`
      case 'FILE_TYPE_UNSUPPORTED':
        return 'Only PDF, DOCX, TXT and MD files are supported.'
      case 'FILE_DUPLICATE':
        return 'You have already uploaded this file.'
      case 'FILE_LIMIT_REACHED':
        return `You can keep up to ${MAX_FILES} files. Delete one to upload another.`
      case 'VALIDATION_FAILED':
        return 'That file is empty or could not be read.'
      case 'HTTP_ERROR':
        // A proxy refused it before the API saw it (the body limit in front of the API is 22 MB).
        if (error.status === 413) return `That file is larger than ${MAX_UPLOAD_MB} MB.`
        break
    }
  }
  return describeProblem(error).message
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
