import { Brain, Globe } from 'lucide-react'
import { useId, useState, type FormEvent } from 'react'
import { Button } from '../../components/ui/Button'
import { Field } from '../../components/ui/Field'
import { useExpired, type CardProps, type Decision, type PendingInterrupt } from './approvals'
import { ResearchPlanCard } from './ResearchPlanCard'

export type { Decision, PendingInterrupt }

type Props = CardProps

const MAX_QUERY = 400 // the API's limit for an edited query

/** The run is waiting for you. Which card to show depends on what it wants to do. */
export function ApprovalCard(props: Props) {
  switch (props.interrupt.tool) {
    case 'forget_memory':
      return <ForgetMemoryCard {...props} />
    case 'research_plan':
      return <ResearchPlanCard {...props} />
    default:
      return <ToolApprovalCard {...props} />
  }
}

type Candidate = { memoryId: string; content: string }

/** The candidates of a forget card, read defensively: anything that is not text is dropped. */
function candidatesOf(interrupt: PendingInterrupt): Candidate[] {
  const raw: unknown = interrupt.args.candidates
  if (!Array.isArray(raw)) return []
  return raw.flatMap((item: unknown): Candidate[] => {
    if (typeof item !== 'object' || item === null) return []
    const { memory_id: memoryId, content } = item as Record<string, unknown>
    return typeof memoryId === 'string' && typeof content === 'string'
      ? [{ memoryId, content }]
      : []
  })
}

/**
 * Nexus wants to forget something it remembers. Nothing is deleted before the user answers: the
 * card shows the exact text of each memory it found (text only, never markup), one candidate to
 * confirm or several to choose from, and either deletes the chosen one or keeps everything.
 */
function ForgetMemoryCard({ interrupt, onDecide, busy = false, now = Date.now }: Props) {
  const titleId = useId()
  const groupId = useId()
  const expired = useExpired(interrupt.expires_at, now)
  const candidates = candidatesOf(interrupt)
  const [chosen, setChosen] = useState<string | null>(null)
  const several = candidates.length > 1
  const selected = several ? chosen : (candidates[0]?.memoryId ?? null)

  return (
    <section
      role="group"
      aria-labelledby={titleId}
      className="mt-4 rounded-lg border border-border bg-surface p-4"
    >
      <h2 id={titleId} className="flex items-center gap-2 text-h5">
        <Brain className="size-4 text-primary" aria-hidden />
        Forget a memory?
      </h2>

      {candidates.length === 0 ? (
        <p className="mt-2 text-body-sm text-fg-muted">
          This approval can&apos;t be shown. You can keep everything to continue.
        </p>
      ) : several ? (
        <fieldset className="mt-2" disabled={busy || expired}>
          <legend id={groupId} className="text-body-sm text-fg-muted">
            Which of these should Nexus forget?
          </legend>
          <ul className="mt-2 space-y-2">
            {candidates.map((candidate) => (
              <li key={candidate.memoryId}>
                <label className="flex items-start gap-2 rounded-md bg-neutral-100 px-3 py-2 text-body break-words dark:bg-neutral-800">
                  <input
                    type="radio"
                    name={groupId}
                    className="mt-1"
                    checked={chosen === candidate.memoryId}
                    onChange={() => setChosen(candidate.memoryId)}
                  />
                  <span>{candidate.content}</span>
                </label>
              </li>
            ))}
          </ul>
        </fieldset>
      ) : (
        <>
          <p className="mt-2 text-body-sm text-fg-muted">Nexus found this memory:</p>
          <p className="mt-2 rounded-md bg-neutral-100 px-3 py-2 text-body break-words dark:bg-neutral-800">
            {candidates[0].content}
          </p>
        </>
      )}

      {expired ? (
        <p role="status" className="mt-3 text-body-sm text-fg-muted">
          This approval expired. Send a new message to continue.
        </p>
      ) : (
        <div className="mt-3 flex gap-2">
          {selected !== null && (
            <Button
              size="sm"
              disabled={busy}
              onClick={() => onDecide({ decision: 'approve', memoryId: selected })}
            >
              Forget {several ? 'the selected memory' : 'it'}
            </Button>
          )}
          {several && selected === null && (
            <Button size="sm" disabled>
              Forget the selected memory
            </Button>
          )}
          <Button
            size="sm"
            variant="secondary"
            disabled={busy}
            onClick={() => onDecide({ decision: 'reject' })}
          >
            {several ? 'Keep them all' : 'Keep it'}
          </Button>
        </div>
      )}
    </section>
  )
}

/**
 * The run is waiting for you: Nexus wants to search the web. Shows the exact query, which can be
 * approved, edited or rejected (nothing is sent to the search provider before that). Text only:
 * the query is rendered as text, never as markup.
 */
function ToolApprovalCard({ interrupt, onDecide, busy = false, now = Date.now }: Props) {
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
