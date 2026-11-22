import { Pencil, Trash2 } from 'lucide-react'
import { useId, useState, type FormEvent } from 'react'
import { Alert } from '../../components/ui/Alert'
import { Button } from '../../components/ui/Button'
import { useAuth } from '../../lib/auth-context'
import { describeProblem } from '../../lib/messages'
import {
  describeMemoryProblem,
  MAX_MEMORY_CHARS,
  MEMORY_CAP,
  SOURCE_LABEL,
  useAddMemory,
  useClearMemories,
  useDeleteMemory,
  useEditMemory,
  useMemories,
  useSetMemoryEnabled,
  type Memory,
} from './memories'

const preview = (text: string) => (text.length > 40 ? `${text.slice(0, 40)}…` : text)

const field =
  'w-full rounded-md border border-border bg-surface px-3 py-2 text-body focus:outline-2 focus:outline-primary'

function MemoryToggle() {
  const { user } = useAuth()
  const set = useSetMemoryEnabled()
  const enabled = user?.memory_enabled ?? false
  const labelId = useId()
  const hintId = useId()

  return (
    <section aria-labelledby={labelId} className="mt-6 rounded-lg border border-border p-4">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 id={labelId} className="text-h5">
            Use long-term memory
          </h2>
          <p id={hintId} className="mt-1 text-body-sm text-fg-muted">
            When on, Nexus learns facts from what you say and uses them in your chats. When off, it
            neither learns new memories nor uses the ones below.
          </p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          aria-labelledby={labelId}
          aria-describedby={hintId}
          disabled={set.isPending}
          onClick={() => set.mutate(!enabled)}
          className={`relative h-6 w-11 shrink-0 rounded-full transition-colors disabled:opacity-60 ${enabled ? 'bg-primary' : 'bg-neutral-300 dark:bg-neutral-700'}`}
        >
          <span
            aria-hidden
            className={`absolute top-0.5 left-0.5 size-5 rounded-full bg-white transition-transform ${enabled ? 'translate-x-5' : ''}`}
          />
        </button>
      </div>
      {set.isError && (
        <p role="alert" className="mt-2 text-caption text-error-fg">
          {describeProblem(set.error).message}
        </p>
      )}
    </section>
  )
}

function AddMemory() {
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const add = useAddMemory()
  const id = useId()

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    try {
      await add.mutateAsync(text)
      setText('')
    } catch (e) {
      setError(describeMemoryProblem(e))
    }
  }

  return (
    <form onSubmit={submit} className="mt-6">
      <label htmlFor={id} className="text-label">
        Add a memory
      </label>
      <div className="mt-1 flex gap-2">
        <input
          id={id}
          value={text}
          maxLength={MAX_MEMORY_CHARS}
          onChange={(e) => setText(e.target.value)}
          placeholder="For example: Prefers short answers"
          aria-invalid={error ? true : undefined}
          className={field}
        />
        <Button type="submit" loading={add.isPending} disabled={text.trim() === ''}>
          Add
        </Button>
      </div>
      {error && (
        <p role="alert" className="mt-1 text-caption text-error-fg">
          {error}
        </p>
      )}
    </form>
  )
}

function MemoryRow({ memory }: { memory: Memory }) {
  const [mode, setMode] = useState<'view' | 'edit' | 'confirm'>('view')
  const [draft, setDraft] = useState(memory.content)
  const [error, setError] = useState<string | null>(null)
  const edit = useEditMemory()
  const remove = useDeleteMemory()
  const editId = useId()

  async function save(event: FormEvent) {
    event.preventDefault()
    setError(null)
    try {
      await edit.mutateAsync({ id: memory.id, content: draft })
      setMode('view')
    } catch (e) {
      setError(describeMemoryProblem(e))
    }
  }

  async function confirmDelete() {
    setError(null)
    try {
      await remove.mutateAsync(memory.id)
    } catch (e) {
      setError(describeMemoryProblem(e))
    }
  }

  if (mode === 'edit') {
    return (
      <li className="border-t border-border py-3">
        <form onSubmit={save}>
          <label htmlFor={editId} className="text-caption text-fg-muted">
            Memory text
          </label>
          <textarea
            id={editId}
            value={draft}
            maxLength={MAX_MEMORY_CHARS}
            rows={2}
            onChange={(e) => setDraft(e.target.value)}
            aria-invalid={error ? true : undefined}
            className={`mt-1 ${field}`}
          />
          {error && (
            <p role="alert" className="mt-1 text-caption text-error-fg">
              {error}
            </p>
          )}
          <div className="mt-2 flex gap-2">
            <Button type="submit" size="sm" loading={edit.isPending} disabled={draft.trim() === ''}>
              Save
            </Button>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={() => {
                setMode('view')
                setDraft(memory.content)
                setError(null)
              }}
            >
              Cancel
            </Button>
          </div>
        </form>
      </li>
    )
  }

  return (
    <li className="flex items-start justify-between gap-4 border-t border-border py-3">
      <div className="min-w-0">
        <p className="text-body break-words">{memory.content}</p>
        <p className="mt-0.5 text-caption text-fg-muted">
          {SOURCE_LABEL[memory.source]} · {new Date(memory.created_at).toLocaleDateString()}
        </p>
        {error && (
          <p role="alert" className="mt-1 text-caption text-error-fg">
            {error}
          </p>
        )}
      </div>
      {mode === 'confirm' ? (
        <div className="flex shrink-0 flex-col items-end gap-1">
          <p className="text-caption">Delete this memory?</p>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="secondary"
              disabled={remove.isPending}
              onClick={() => {
                setMode('view')
                setError(null)
              }}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              loading={remove.isPending}
              onClick={confirmDelete}
              aria-label={`Confirm delete: ${preview(memory.content)}`}
            >
              Delete
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex shrink-0 gap-1">
          <Button
            size="sm"
            variant="ghost"
            aria-label={`Edit: ${preview(memory.content)}`}
            onClick={() => {
              setDraft(memory.content)
              setMode('edit')
            }}
          >
            <Pencil className="size-4" aria-hidden />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            aria-label={`Delete: ${preview(memory.content)}`}
            onClick={() => setMode('confirm')}
          >
            <Trash2 className="size-4" aria-hidden />
          </Button>
        </div>
      )}
    </li>
  )
}

function ClearAll({ count }: { count: number }) {
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const clear = useClearMemories()

  async function confirm() {
    setError(null)
    try {
      await clear.mutateAsync()
      setConfirming(false)
    } catch (e) {
      setError(describeMemoryProblem(e))
    }
  }

  if (!confirming) {
    return (
      <Button
        variant="secondary"
        size="sm"
        disabled={count === 0}
        onClick={() => setConfirming(true)}
      >
        Clear all
      </Button>
    )
  }
  return (
    <div className="flex flex-col items-end gap-1">
      <p className="text-caption">
        Delete all {count} {count === 1 ? 'memory' : 'memories'}? This cannot be undone.
      </p>
      <div className="flex gap-2">
        <Button
          size="sm"
          variant="secondary"
          disabled={clear.isPending}
          onClick={() => {
            setConfirming(false)
            setError(null)
          }}
        >
          Cancel
        </Button>
        <Button size="sm" loading={clear.isPending} onClick={confirm}>
          Delete all
        </Button>
      </div>
      {error && (
        <p role="alert" className="text-caption text-error-fg">
          {error}
        </p>
      )}
    </div>
  )
}

/** `/settings/memory`: the switch, what Nexus remembers, edit and delete (functional spec UF-006). */
export function MemoryPage() {
  const memories = useMemories()
  const count = memories.data?.length ?? 0

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <header className="flex items-baseline justify-between gap-4">
        <h1 className="text-h2">Memory</h1>
        {memories.data && (
          <p className="text-body-sm text-fg-muted" aria-label="Memories used">
            {count}/{MEMORY_CAP} memories
          </p>
        )}
      </header>
      <p className="mt-1 text-body text-fg-muted">
        Facts Nexus keeps about you so you do not have to repeat yourself. They are private to you.
        Health, religion, sexuality, politics, precise location and credentials are never
        remembered.
      </p>

      <MemoryToggle />
      <AddMemory />

      <section aria-label="Your memories" className="mt-8">
        <div className="mb-2 flex items-center justify-between gap-4">
          <h2 className="text-h5">What Nexus remembers</h2>
          <ClearAll count={count} />
        </div>
        {memories.isPending ? (
          <div role="status" aria-label="Loading memories" className="py-8" />
        ) : memories.isError ? (
          <Alert variant="error">
            <p>{describeProblem(memories.error).message}</p>
            <Button
              size="sm"
              variant="secondary"
              className="mt-2"
              onClick={() => memories.refetch()}
            >
              Try again
            </Button>
          </Alert>
        ) : count === 0 ? (
          <p className="py-8 text-center text-fg-muted">
            Nothing yet. Nexus will add facts you share in chats, or add one above.
          </p>
        ) : (
          <ul aria-label="Memories">
            {memories.data.map((m) => (
              <MemoryRow key={m.id} memory={m} />
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
