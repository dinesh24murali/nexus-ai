import { Plus, Search, Trash2 } from 'lucide-react'
import { useId, useState, type FormEvent } from 'react'
import { Button } from '../../components/ui/Button'
import { Field } from '../../components/ui/Field'
import { useExpired, type CardProps } from './approvals'

const MAX_QUERIES = 6 // the API's limits for an edited plan
const MAX_QUERY = 400

/** The proposed queries, read defensively: anything that is not text is dropped. */
function queriesOf(args: Record<string, unknown>): string[] {
  const raw: unknown = args.queries
  return Array.isArray(raw)
    ? raw.filter((q: unknown): q is string => typeof q === 'string' && q.trim() !== '')
    : []
}

/**
 * Nexus has planned some web research and asks before searching: the topic and the exact queries
 * it wants to run. Nothing is sent to the search provider before the user answers, and only the
 * queries approved here are ever searched (nothing is added afterwards). The user can approve
 * them, edit, add (up to 6) or remove queries, or reject the plan. Text only: a topic or query is
 * rendered as text, never as markup.
 */
export function ResearchPlanCard({ interrupt, onDecide, busy = false, now = Date.now }: CardProps) {
  const titleId = useId()
  const expired = useExpired(interrupt.expires_at, now)
  const topic = typeof interrupt.args.topic === 'string' ? interrupt.args.topic : ''
  const proposed = queriesOf(interrupt.args)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<string[]>(proposed)
  const cleaned = draft.map((q) => q.trim()).filter((q) => q !== '')

  function submitEdit(event?: FormEvent) {
    event?.preventDefault()
    if (busy || cleaned.length === 0) return
    const same = cleaned.length === proposed.length && cleaned.every((q, i) => q === proposed[i])
    onDecide(same ? { decision: 'approve' } : { decision: 'edit', queries: cleaned })
  }

  return (
    <section
      role="group"
      aria-labelledby={titleId}
      className="mt-4 rounded-lg border border-border bg-surface p-4"
    >
      <h2 id={titleId} className="flex items-center gap-2 text-h5">
        <Search className="size-4 text-primary" aria-hidden />
        Research plan
      </h2>

      {proposed.length === 0 ? (
        <>
          <p className="mt-2 text-body-sm text-fg-muted">
            This plan can&apos;t be shown. You can reject it to continue.
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
          {topic && (
            <p className="mt-2 text-body-sm text-fg-muted">
              Topic: <span className="text-fg break-words">{topic}</span>
            </p>
          )}
          <p className="mt-2 text-body-sm text-fg-muted">
            Nexus wants to search the web for these. Nothing is searched until you approve, and
            nothing else will be searched.
          </p>

          {editing && !expired ? (
            <form onSubmit={submitEdit} className="mt-2">
              <ul className="space-y-2">
                {draft.map((query, index) => (
                  <li key={index} className="flex items-end gap-2">
                    <div className="min-w-0 flex-1">
                      <Field
                        label={`Search query ${index + 1}`}
                        value={query}
                        maxLength={MAX_QUERY}
                        autoFocus={index === 0}
                        onChange={(event) =>
                          setDraft((d) => d.map((q, i) => (i === index ? event.target.value : q)))
                        }
                      />
                    </div>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-label={`Remove query ${index + 1}`}
                      disabled={busy || draft.length === 1}
                      onClick={() => setDraft((d) => d.filter((_, i) => i !== index))}
                    >
                      <Trash2 className="size-4" aria-hidden />
                    </Button>
                  </li>
                ))}
              </ul>
              <div className="mt-2">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={busy || draft.length >= MAX_QUERIES}
                  onClick={() => setDraft((d) => [...d, ''])}
                >
                  <Plus className="size-4" aria-hidden />
                  Add a query
                </Button>
              </div>
              <div className="mt-3 flex gap-2">
                <Button type="submit" size="sm" disabled={busy || cleaned.length === 0}>
                  Search with these queries
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
            <ol className="mt-2 list-decimal space-y-1 rounded-md bg-neutral-100 py-2 pr-3 pl-8 text-body break-words dark:bg-neutral-800">
              {proposed.map((query, index) => (
                <li key={index}>{query}</li>
              ))}
            </ol>
          )}

          {expired ? (
            <p role="status" className="mt-3 text-body-sm text-fg-muted">
              This approval expired. Send a new message to continue.
            </p>
          ) : (
            !editing && (
              <div className="mt-3 flex gap-2">
                <Button size="sm" disabled={busy} onClick={() => onDecide({ decision: 'approve' })}>
                  Approve and search
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
                  Don&apos;t search
                </Button>
              </div>
            )
          )}
        </>
      )}
    </section>
  )
}
