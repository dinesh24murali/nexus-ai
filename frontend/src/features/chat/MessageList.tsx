import { AlertTriangle, RefreshCw } from 'lucide-react'
import { Button } from '../../components/ui/Button'
import { cn } from '../../lib/cn'
import type { Message } from './api'
import { Feedback } from './Feedback'
import { Markdown } from './Markdown'
import type { ChatRun } from './useChatRun'

function UserBubble({ text }: { text: string }) {
  return (
    <article aria-label="Your message" className="flex justify-end">
      <div className="max-w-[85%] rounded-lg bg-user-bubble px-4 py-3 text-body-lg break-words whitespace-pre-wrap">
        {text}
      </div>
    </article>
  )
}

function Label({ children, tone }: { children: string; tone: 'muted' | 'warning' }) {
  return (
    <p
      className={cn(
        'mt-2 flex items-center gap-1.5 text-caption',
        tone === 'warning' ? 'text-warning-fg' : 'text-fg-muted',
      )}
    >
      {tone === 'warning' && <AlertTriangle className="size-3.5" aria-hidden />}
      {children}
    </p>
  )
}

type AssistantProps = {
  message: Message
  isLast: boolean
  canRetry: boolean
  onRegenerate: () => void
}

function AssistantBubble({ message, isLast, canRetry, onRegenerate }: AssistantProps) {
  const failed = message.status === 'failed'
  const interrupted = failed && message.finish_reason === 'server_restart'
  // A failed answer is not an answer to rate, and an empty one has nothing to rate.
  const ratable = !failed && message.content !== ''
  return (
    <article aria-label="Nexus reply" className="text-body-lg">
      {message.content && <Markdown text={message.content} />}
      {message.status === 'cancelled' && <Label tone="muted">Stopped</Label>}
      {failed && (
        <Label tone="warning">
          {interrupted
            ? 'Interrupted: the server restarted while answering.'
            : "This answer didn't finish."}
        </Label>
      )}
      {(ratable || (isLast && canRetry)) && (
        <div className="mt-2 flex items-center gap-1 -ml-2">
          {ratable && <Feedback message={message} />}
          {isLast && canRetry && (
            <Button variant="ghost" size="sm" onClick={onRegenerate}>
              <RefreshCw className="size-3.5" aria-hidden />
              {failed ? 'Retry' : 'Regenerate'}
            </Button>
          )}
        </div>
      )}
    </article>
  )
}

type Props = {
  /** Oldest first. */
  messages: Message[]
  run: ChatRun
  hasEarlier: boolean
  loadingEarlier: boolean
  onLoadEarlier: () => void
  onRegenerate: () => void
}

export function MessageList({
  messages,
  run,
  hasEarlier,
  loadingEarlier,
  onLoadEarlier,
  onRegenerate,
}: Props) {
  // The live view and the stored rows overlap for a moment when a run ends: the stored rows are
  // read first, then the run is marked finished. Anything the server has already stored is shown
  // from the stored rows only, so the swap happens in one render and nothing appears twice.
  const stored = new Set(messages.map((m) => m.id))
  const userStored = run.userMessageId !== null && stored.has(run.userMessageId)
  const answerStored = run.assistantMessageId !== null && stored.has(run.assistantMessageId)
  // While a regenerate streams, the answer it replaces is hidden (the server swaps it at the end).
  const shown = messages.filter((m) => m.id !== run.replacesMessageId)
  const last = shown.at(-1)
  const canRetry = !run.running

  return (
    <div
      role="log"
      aria-label="Conversation"
      aria-live="polite"
      aria-busy={run.running}
      className="flex flex-col gap-6"
    >
      {hasEarlier && (
        <Button
          variant="ghost"
          size="sm"
          className="self-center"
          onClick={onLoadEarlier}
          loading={loadingEarlier}
        >
          Load earlier messages
        </Button>
      )}
      {shown.map((message) =>
        message.role === 'user' ? (
          <UserBubble key={message.id} text={message.content} />
        ) : (
          <AssistantBubble
            key={message.id}
            message={message}
            isLast={message === last}
            canRetry={canRetry}
            onRegenerate={onRegenerate}
          />
        ),
      )}
      {run.pendingUser !== null && !userStored && <UserBubble text={run.pendingUser} />}
      {run.running && !answerStored && (
        <article aria-label="Nexus reply" className="text-body-lg">
          {run.streamingText ? (
            <Markdown text={run.streamingText} streaming />
          ) : (
            <p role="status" className="text-fg-muted">
              {run.stopping ? 'Stopping…' : 'Thinking…'}
            </p>
          )}
        </article>
      )}
    </div>
  )
}
