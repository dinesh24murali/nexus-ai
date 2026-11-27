import { Check, Copy } from 'lucide-react'
import { useEffect, useState } from 'react'
import { cn } from '../../lib/cn'
import { highlight, type Token, type TokenKind } from './highlight'

// Colours come from the design tokens only (docs/02_outputs/07_design_system.md); the code
// background is dark in both themes, so these are the light-on-dark picks.
const KIND_CLASS: Record<TokenKind, string> = {
  plain: '',
  keyword: 'text-accent',
  string: 'text-secondary',
  comment: 'text-neutral-400',
}

type Props = {
  code: string
  language: string | undefined
  /** Highlighting is skipped while the answer is still being written (the code keeps changing). */
  highlighting?: boolean
}

export function CodeBlock({ code, language, highlighting = true }: Props) {
  const key = `${language ?? ''}\u0000${code}`
  const [result, setResult] = useState<{ key: string; lines: Token[][] | null } | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!highlighting) return
    let current = true
    highlight(code, language)
      .then((lines) => current && setResult({ key, lines }))
      .catch(() => undefined) // a grammar that fails to load just stays plain text
    return () => {
      current = false
    }
  }, [code, language, highlighting, key])

  useEffect(() => {
    if (!copied) return
    const timer = setTimeout(() => setCopied(false), 2000)
    return () => clearTimeout(timer)
  }, [copied])

  const lines = highlighting && result?.key === key ? result.lines : null

  async function copy() {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
    } catch {
      // Clipboard access can be denied (insecure context, permissions); the text stays selectable.
    }
  }

  return (
    <div className="my-4 overflow-hidden rounded-lg border border-neutral-800 bg-code-bg text-neutral-100">
      <div className="flex items-center justify-between px-4 py-2 text-caption text-neutral-400">
        <span>{language ?? 'text'}</span>
        <button
          type="button"
          onClick={copy}
          aria-label={copied ? 'Copied' : 'Copy code'}
          className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-neutral-800 hover:text-neutral-100"
        >
          {copied ? (
            <Check className="size-3.5" aria-hidden />
          ) : (
            <Copy className="size-3.5" aria-hidden />
          )}
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre className="overflow-x-auto px-4 pb-4 font-mono text-code">
        <code>
          {lines
            ? lines.map((line, i) => (
                <span key={i}>
                  {line.map((token, j) => (
                    <span key={j} className={cn(KIND_CLASS[token.kind])}>
                      {token.text}
                    </span>
                  ))}
                  {i < lines.length - 1 && '\n'}
                </span>
              ))
            : code}
        </code>
      </pre>
    </div>
  )
}
