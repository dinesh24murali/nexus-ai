import { Globe } from 'lucide-react'
import { useEffect, useId, useState, type FormEvent } from 'react'
import type { components } from '../../api/schema'
import { Button } from '../../components/ui/Button'
import { Field } from '../../components/ui/Field'

export type PendingInterrupt = components['schemas']['PendingInterrupt']

/** What the user decided. `edit` carries the replacement query (web search). */
export type Decision =
  { decision: 'approve' } | { decision: 'reject' } | { decision: 'edit'; query: string }

type Props = {
  interrupt: PendingInterrupt
  onDecide: (decision: Decision) => void
  /** A decision is on its way: the buttons wait, so one click cannot be sent twice. */
  busy?: boolean
  /** The clock, so tests can fix the time. */
  now?: () => number
}

const MAX_QUERY = 400 // the API's limit for an edited query
const MAX_TIMER_MS = 2 ** 31 - 1 // setTimeout overflows above this

/**
 * True once `expiresAt` has passed, flipping at that moment without a reload. A timer cannot wait
 * longer than `MAX_TIMER_MS`, so a far deadline is waited for in several steps.
 */
function useExpired(expiresAt: string, now: () => number): boolean {
  const deadline = new Date(expiresAt).getTime()
  const [current, setCurrent] = useState(now)
  const expired = !(deadline > current)
  useEffect(() => {
    if (expired) return
    const timer = window.setTimeout(
      () => setCurrent(now()),
      Math.min(deadline - now() + 1, MAX_TIMER_MS),
    )
    return () => clearTimeout(timer)
  }, [deadline, now, expired, current])
  return expired
}

/**
 * The run is waiting for you: Nexus wants to search the web. Shows the exact query, which can be
 * approved, edited or rejected (nothing is sent to the search provider before that). Text only:
 * the query is rendered as text, never as markup.
 */
export function ApprovalCard({ interrupt, onDecide, busy = false, now = Date.now }: Props) {
  const titleId = useId()
  const expired = useExpired(interrupt.expires_at, now)
  const proposed = typeof interrupt.args.query === 'string' ? interrupt.args.query : ''
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(proposed)
  const supported = interrupt.tool === 'web_search'
  const query = draft.trim()

  function submitEdit(event?: FormEvent) {
    event?.preventDefault()
    if (busy || !query) return
    onDecide(query === proposed ? { decision: 'approve' } : { decision: 'edit', query })
  }

  return (
    <section
      role="group"
      aria-labelledby={titleId}
      className="mt-4 rounded-lg border border-border bg-surface p-4"
    >
      <h2 id={titleId} className="flex items-center gap-2 text-h5">
        <Globe className="size-4 text-primary" aria-hidden />
        {supported ? 'Search the web?' : 'Approval needed'}
      </h2>

      {!supported ? (
        <>
          <p className="mt-2 text-body-sm text-fg-muted">
            This kind of approval can&apos;t be shown here yet. You can reject it to continue.
          </p>
          <div className="mt-3">
            <Button
              size="sm"
              variant="secondary"
              disabled={busy}
              onClick={() => onDecide({ decision: 'reject' })}
            >
              Reject
            </Button>
          </div>
        </>
      ) : (
        <>
          <p className="mt-2 text-body-sm text-fg-muted">Nexus wants to search the web for:</p>
          {editing && !expired ? (
            <form onSubmit={submitEdit} className="mt-2">
              <Field
                label="Search query"
                value={draft}
                maxLength={MAX_QUERY}
                autoFocus
                onChange={(event) => setDraft(event.target.value)}
              />
              <div className="mt-3 flex gap-2">
                <Button type="submit" size="sm" disabled={busy || !query}>
                  Search with this query
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => {
                    setEditing(false)
                    setDraft(proposed)
                  }}
                >
                  Cancel
                </Button>
              </div>
            </form>
          ) : (
            <p className="mt-2 rounded-md bg-neutral-100 px-3 py-2 text-body break-words dark:bg-neutral-800">
              {proposed}
            </p>
          )}

          {expired ? (
            <p role="status" className="mt-3 text-body-sm text-fg-muted">
              This approval expired. Send a new message to continue.
            </p>
          ) : (
            !editing && (
              <div className="mt-3 flex gap-2">
                <Button size="sm" disabled={busy} onClick={() => onDecide({ decision: 'approve' })}>
                  Approve
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={busy}
                  onClick={() => setEditing(true)}
                >
                  Edit
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => onDecide({ decision: 'reject' })}
                >
                  Reject
                </Button>
              </div>
            )
          )}
        </>
      )}
    </section>
  )
}
