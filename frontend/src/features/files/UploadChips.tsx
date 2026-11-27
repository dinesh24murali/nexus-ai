import { AlertCircle, CheckCircle2, Loader2, X } from 'lucide-react'
import { cn } from '../../lib/cn'
import { useFiles, type FileObject } from './api'
import type { Attachment, Attachments } from './uploads'

type Label = { text: string; tone: 'muted' | 'ok' | 'bad'; busy: boolean }
type Row = { item: Attachment; label: Label }

function labelOf(file: FileObject): Label {
  if (file.status === 'ready') return { text: 'Ready', tone: 'ok', busy: false }
  if (file.status === 'failed') {
    return { text: `Failed: ${file.error ?? 'could not be processed'}`, tone: 'bad', busy: false }
  }
  return { text: 'Processing…', tone: 'muted', busy: true }
}

/**
 * What this tab uploaded: Uploading… then Processing… then Ready, or the reason it failed
 * (functional spec UF-004). Uploaded files take their state from the polled files list.
 * `onlyInFlight` hides finished uploads (the files page already lists those in its table).
 */
export function UploadChips({
  attachments,
  onlyInFlight = false,
}: {
  attachments: Attachments
  onlyInFlight?: boolean
}) {
  const { items, notice, dismiss } = attachments
  // Nothing to look up (and no request) until something of ours has been uploaded.
  const { data: files } = useFiles({ enabled: items.some((item) => item.state === 'uploaded') })

  const rows = items.flatMap((item): Row[] => {
    if (item.state === 'uploading') {
      return [{ item, label: { text: 'Uploading…', tone: 'muted', busy: true } }]
    }
    if (item.state === 'error') {
      return [{ item, label: { text: item.message, tone: 'bad', busy: false } }]
    }
    if (onlyInFlight) return []
    const file = files?.find((f) => f.id === item.fileId)
    if (!file) return [] // deleted meanwhile, or the list has not loaded yet
    return [{ item, label: labelOf(file) }]
  })

  if (rows.length === 0 && !notice) return null
  return (
    <div className="mb-2">
      {notice && (
        <p role="status" className="mb-1 text-caption text-warning-fg">
          {notice}
        </p>
      )}
      <ul aria-label="Uploads" className="flex flex-wrap gap-2">
        {rows.map(({ item, label }) => (
          <li
            key={item.key}
            className={cn(
              'flex max-w-full items-center gap-2 rounded-md border px-2 py-1 text-caption',
              label.tone === 'bad' ? 'border-error-fg/40 text-error-fg' : 'border-border',
            )}
          >
            {label.busy ? (
              <Loader2 className="size-3.5 shrink-0 animate-spin" aria-hidden />
            ) : label.tone === 'ok' ? (
              <CheckCircle2 className="size-3.5 shrink-0 text-success-fg" aria-hidden />
            ) : (
              <AlertCircle className="size-3.5 shrink-0" aria-hidden />
            )}
            <span className="truncate font-medium" title={item.name}>
              {item.name}
            </span>
            <span className={label.tone === 'muted' ? 'text-fg-muted' : undefined}>
              {label.text}
            </span>
            {!label.busy && (
              <button
                type="button"
                aria-label={`Dismiss ${item.name}`}
                onClick={() => dismiss(item.key)}
                className="rounded-sm text-fg-muted hover:text-fg"
              >
                <X className="size-3.5" aria-hidden />
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
