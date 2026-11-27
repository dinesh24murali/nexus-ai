import { Check, MoreHorizontal, Pencil, Trash2, X } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { NavLink } from 'react-router'
import {
  useDeleteConversation,
  useRenameConversation,
  type Conversation,
} from '../../features/conversations/api'
import { cn } from '../../lib/cn'
import { describeProblem } from '../../lib/messages'
import { Button } from '../ui/Button'

const TITLE_MAX = 100

type Mode = 'idle' | 'renaming' | 'confirming-delete'

export function ConversationItem({
  conversation,
  active,
  onDeleted,
}: {
  conversation: Conversation
  active: boolean
  onDeleted: (id: string) => void
}) {
  const [mode, setMode] = useState<Mode>('idle')
  const [menuOpen, setMenuOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const rename = useRenameConversation()
  const remove = useDeleteConversation()
  const menuRef = useRef<HTMLDivElement>(null)
  const menuButtonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!menuOpen) return
    const close = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [menuOpen])

  const finish = () => {
    setMode('idle')
    setError(null)
  }

  async function confirmDelete() {
    try {
      await remove.mutateAsync(conversation.id)
      onDeleted(conversation.id)
    } catch (e) {
      setError(describeProblem(e).message)
    }
  }

  if (mode === 'renaming') {
    return (
      <li>
        <RenameForm
          initial={conversation.title}
          saving={rename.isPending}
          error={error}
          onCancel={finish}
          onSave={async (title) => {
            if (title === conversation.title) return finish()
            try {
              await rename.mutateAsync({ id: conversation.id, title })
              finish()
            } catch (e) {
              setError(describeProblem(e).message)
            }
          }}
        />
      </li>
    )
  }

  if (mode === 'confirming-delete') {
    return (
      <li
        className="rounded-md bg-surface p-2 text-body-sm"
        role="group"
        aria-label="Confirm delete"
      >
        <p className="px-1">
          Delete <strong className="font-semibold">{conversation.title}</strong>? This cannot be
          undone.
        </p>
        {error && (
          <p role="alert" className="mt-1 px-1 text-error-fg">
            {error}
          </p>
        )}
        <div className="mt-2 flex gap-2">
          <Button size="sm" variant="secondary" onClick={finish} disabled={remove.isPending}>
            Cancel
          </Button>
          <Button size="sm" onClick={confirmDelete} loading={remove.isPending}>
            Delete
          </Button>
        </div>
      </li>
    )
  }

  const onMenuKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      setMenuOpen(false)
      menuButtonRef.current?.focus()
    }
  }

  return (
    <li className="group relative" onKeyDown={onMenuKey}>
      <NavLink
        to={`/c/${conversation.id}`}
        aria-current={active ? 'page' : undefined}
        title={conversation.title}
        className={cn(
          'block truncate rounded-md py-2 pr-9 pl-3 text-body-sm text-fg hover:bg-neutral-200/60 dark:hover:bg-neutral-800',
          active && 'bg-neutral-200 font-medium dark:bg-neutral-800',
        )}
      >
        {conversation.title}
      </NavLink>
      <div ref={menuRef} className="absolute top-1/2 right-1 -translate-y-1/2">
        <button
          ref={menuButtonRef}
          type="button"
          aria-label={`Actions for ${conversation.title}`}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((open) => !open)}
          className={cn(
            'flex size-7 items-center justify-center rounded-md text-fg-muted hover:bg-neutral-300/60 hover:text-fg focus-visible:opacity-100 dark:hover:bg-neutral-700',
            active || menuOpen
              ? 'opacity-100'
              : 'opacity-0 group-focus-within:opacity-100 group-hover:opacity-100',
          )}
        >
          <MoreHorizontal className="size-4" aria-hidden />
        </button>
        {menuOpen && (
          <div
            role="menu"
            aria-label={`${conversation.title} actions`}
            className="absolute right-0 z-10 mt-1 w-36 rounded-md border border-border bg-surface p-1 shadow-md"
          >
            <button
              type="button"
              role="menuitem"
              autoFocus
              className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-body-sm hover:bg-neutral-100 dark:hover:bg-neutral-800"
              onClick={() => {
                setMenuOpen(false)
                setMode('renaming')
              }}
            >
              <Pencil className="size-4" aria-hidden />
              Rename
            </button>
            <button
              type="button"
              role="menuitem"
              className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-body-sm text-error-fg hover:bg-neutral-100 dark:hover:bg-neutral-800"
              onClick={() => {
                setMenuOpen(false)
                setMode('confirming-delete')
              }}
            >
              <Trash2 className="size-4" aria-hidden />
              Delete
            </button>
          </div>
        )}
      </div>
    </li>
  )
}

function RenameForm({
  initial,
  saving,
  error,
  onSave,
  onCancel,
}: {
  initial: string
  saving: boolean
  error: string | null
  onSave: (title: string) => void
  onCancel: () => void
}) {
  const [value, setValue] = useState(initial)
  const trimmed = value.trim()
  const valid = trimmed.length >= 1 && trimmed.length <= TITLE_MAX

  function submit(e: FormEvent) {
    e.preventDefault()
    if (valid && !saving) onSave(trimmed)
  }

  return (
    <form onSubmit={submit} className="px-1">
      <div className="flex items-center gap-1">
        <input
          aria-label="Conversation title"
          aria-invalid={!valid || undefined}
          autoFocus
          onFocus={(e) => e.currentTarget.select()}
          value={value}
          maxLength={TITLE_MAX}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') onCancel()
          }}
          className="h-8 min-w-0 flex-1 rounded-md border border-border bg-surface px-2 text-body-sm text-fg"
        />
        <button
          type="submit"
          aria-label="Save title"
          disabled={!valid || saving}
          className="flex size-8 items-center justify-center rounded-md text-primary hover:bg-neutral-200/60 disabled:opacity-60 dark:hover:bg-neutral-800"
        >
          <Check className="size-4" aria-hidden />
        </button>
        <button
          type="button"
          aria-label="Cancel rename"
          onClick={onCancel}
          className="flex size-8 items-center justify-center rounded-md text-fg-muted hover:bg-neutral-200/60 dark:hover:bg-neutral-800"
        >
          <X className="size-4" aria-hidden />
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-1 text-body-sm text-error-fg">
          {error}
        </p>
      )}
    </form>
  )
}
