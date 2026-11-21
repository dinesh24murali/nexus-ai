import { useEffect, useId, useRef, useState } from 'react'
import { cn } from '../../lib/cn'
import type { Message } from './api'

type Citation = NonNullable<Message['citations']>[number]

/** Only http(s) addresses become links: a model-supplied `javascript:` URL is never one. */
function safeUrl(raw: string | null | undefined): string | null {
  if (!raw) return null
  try {
    const url = new URL(raw)
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null
  } catch {
    return null
  }
}

const labelOf = (c: Citation) => (c.page ? `${c.title} p.${c.page}` : c.title)

function Chip({
  citation,
  open,
  onToggle,
}: {
  citation: Citation
  open: boolean
  onToggle: () => void
}) {
  const popover = useId()
  const link = citation.source_type === 'web' ? safeUrl(citation.url) : null
  return (
    <li className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? popover : undefined}
        onClick={onToggle}
        className="inline-flex max-w-64 items-center gap-1.5 rounded-md border border-border bg-surface px-2 py-1 text-caption hover:bg-neutral-100 dark:hover:bg-neutral-800"
      >
        <span className="font-medium text-primary">[{citation.index}]</span>{' '}
        <span className="truncate">{labelOf(citation)}</span>
      </button>
      {open && (
        <div
          id={popover}
          role="dialog"
          aria-label={`Source ${citation.index}: ${citation.title}`}
          className="absolute bottom-full left-0 z-10 mb-2 w-80 max-w-[80vw] rounded-lg border border-border bg-surface p-3 text-body-sm shadow-lg"
        >
          <p className="font-medium break-words">{labelOf(citation)}</p>
          {citation.snippet ? (
            <p className="mt-2 break-words whitespace-pre-wrap text-fg-muted">{citation.snippet}</p>
          ) : (
            <p className="mt-2 text-fg-muted">No excerpt available.</p>
          )}
          {link && (
            <a
              href={link}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-2 inline-block text-primary underline underline-offset-2"
            >
              Open source
            </a>
          )}
        </div>
      )}
    </li>
  )
}

/**
 * The sources an answer cites, as chips under it (functional spec UF-004): `[1] q3-report.pdf p.4`.
 * A chip opens the excerpt the answer was based on. Everything here is data from the model's
 * tools, rendered as plain text (never as markup).
 */
export function Citations({ citations, className }: { citations: Citation[]; className?: string }) {
  const [openIndex, setOpenIndex] = useState<number | null>(null)
  const root = useRef<HTMLUListElement>(null)

  useEffect(() => {
    if (openIndex === null) return
    const onPointer = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpenIndex(null)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpenIndex(null)
    }
    document.addEventListener('mousedown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [openIndex])

  if (citations.length === 0) return null
  return (
    <ul ref={root} aria-label="Sources" className={cn('mt-3 flex flex-wrap gap-2', className)}>
      {citations.map((citation) => (
        <Chip
          key={citation.index}
          citation={citation}
          open={openIndex === citation.index}
          onToggle={() => setOpenIndex(openIndex === citation.index ? null : citation.index)}
        />
      ))}
    </ul>
  )
}
