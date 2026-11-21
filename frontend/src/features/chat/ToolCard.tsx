import { AlertCircle, Ban, CheckCircle2, Clock, Loader2 } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '../../lib/cn'
import { argLines, KNOWN, labelOf, type ToolCallView, type ToolStatus } from './toolCalls'

function Icon({ status }: { status: ToolStatus }): ReactNode {
  switch (status) {
    case 'running':
      return <Loader2 className="size-3.5 shrink-0 animate-spin" aria-hidden />
    case 'completed':
      return <CheckCircle2 className="size-3.5 shrink-0 text-success-fg" aria-hidden />
    case 'rejected':
      return <Ban className="size-3.5 shrink-0 text-fg-muted" aria-hidden />
    case 'timed_out':
      return <Clock className="size-3.5 shrink-0 text-warning-fg" aria-hidden />
    case 'failed':
      return <AlertCircle className="size-3.5 shrink-0 text-error-fg" aria-hidden />
  }
}

/**
 * What Nexus did with a tool: its name in plain words, the arguments (the search query or the
 * sum), and how it went (functional spec FS-005). Everything shown comes from the model's call or
 * a tool's summary, so it is rendered as text only.
 */
export function ToolCard({ call }: { call: ToolCallView }) {
  const label = labelOf(call)
  const headlineKey = KNOWN[call.name]?.headline
  const raw = headlineKey ? call.args[headlineKey] : undefined
  const headline = typeof raw === 'string' && raw !== '' ? raw : null
  const extra = argLines(call)
  const bad = call.status === 'failed' || call.status === 'timed_out'
  return (
    <div
      role="group"
      aria-label={label}
      data-status={call.status}
      className={cn(
        'rounded-md border bg-surface px-3 py-2 text-body-sm',
        bad ? 'border-error-fg/40' : 'border-border',
      )}
    >
      <p
        role={call.status === 'running' ? 'status' : undefined}
        className="flex items-center gap-2 font-medium"
      >
        <Icon status={call.status} />
        <span>{label}</span>
      </p>
      {headline && <p className="mt-1 font-mono text-body-sm break-words">{headline}</p>}
      {extra.length > 0 && (
        <dl className="mt-1 font-mono text-caption text-fg-muted">
          {extra.map(({ key, value }) => (
            <div key={key} className="flex gap-1.5 break-words">
              <dt>{key}:</dt> <dd className="break-all">{value}</dd>
            </div>
          ))}
        </dl>
      )}
      {call.summary && (
        <p className={cn('mt-1 text-caption break-words', bad ? 'text-error-fg' : 'text-fg-muted')}>
          {call.summary}
        </p>
      )}
    </div>
  )
}

/** The calls of one answer, in the order they were made. Nothing when there were none. */
export function ToolCalls({ calls, className }: { calls: ToolCallView[]; className?: string }) {
  if (calls.length === 0) return null
  return (
    <ul aria-label="Tool calls" className={cn('flex flex-col gap-2', className)}>
      {calls.map((call) => (
        <li key={call.id}>
          <ToolCard call={call} />
        </li>
      ))}
    </ul>
  )
}
