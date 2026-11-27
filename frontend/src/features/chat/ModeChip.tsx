import type { Message } from './api'
import { modeLabel, type RouteSource } from './modes'

const HOW: Record<RouteSource, string> = {
  command: 'chosen by your command',
  router: 'chosen automatically from your message',
  fallback: 'chosen automatically',
}

/** Which pipeline answered, when it was not plain chat. Text only. */
export function ModeChip({
  intent,
  source,
}: {
  intent: Message['intent'] | undefined
  source?: Message['route_source']
}) {
  const label = modeLabel(intent)
  if (!label) return null
  return (
    <p className="mb-2">
      <span
        title={source ? HOW[source] : undefined}
        className="inline-flex items-center rounded-md border border-border bg-surface px-2 py-0.5 text-caption font-medium text-fg-muted"
      >
        <span className="sr-only">Answered by: </span>
        {label}
      </span>
    </p>
  )
}
