import { Children, isValidElement, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CodeBlock } from './CodeBlock'

/**
 * Model output is untrusted. This renders it without ever producing HTML from the text itself:
 * raw HTML in the markdown is shown as literal text (no `rehype-raw`), `javascript:` and other
 * unknown URL schemes are dropped by react-markdown's default `urlTransform`, links open in a new
 * tab with `rel="noopener noreferrer"`, and images are never fetched (a model-chosen URL would be
 * a way to leak the conversation, and the CSP would block it anyway). No `dangerouslySetInnerHTML`.
 */

function languageOf(className: string | undefined): string | undefined {
  return /language-(\S+)/.exec(className ?? '')?.[1]
}

function textOf(node: ReactNode): string {
  return Children.toArray(node)
    .map((child) => (typeof child === 'string' || typeof child === 'number' ? String(child) : ''))
    .join('')
}

function components(streaming: boolean): Components {
  return {
    // Answer headings sit below the page's own h1 (the conversation title); sizes follow the
    // design system's mapping of markdown levels (doc 07: `#` = H3, `##` = H4, `###` = H5).
    h1: ({ children }) => <h2 className="mt-6 mb-2 text-h3">{children}</h2>,
    h2: ({ children }) => <h3 className="mt-5 mb-2 text-h4">{children}</h3>,
    h3: ({ children }) => <h4 className="mt-4 mb-1 text-h5">{children}</h4>,
    h4: ({ children }) => <h5 className="mt-4 mb-1 text-h6">{children}</h5>,
    h5: ({ children }) => <h5 className="mt-4 mb-1 text-h6">{children}</h5>,
    h6: ({ children }) => <h5 className="mt-4 mb-1 text-h6">{children}</h5>,
    p: ({ children }) => <p className="my-3 break-words">{children}</p>,
    ul: ({ children }) => <ul className="my-3 list-disc space-y-1 pl-6">{children}</ul>,
    ol: ({ children }) => <ol className="my-3 list-decimal space-y-1 pl-6">{children}</ol>,
    blockquote: ({ children }) => (
      <blockquote className="my-3 border-l-2 border-border pl-4 text-fg-muted">
        {children}
      </blockquote>
    ),
    hr: () => <hr className="my-6 border-border" />,
    a: ({ href, children }) => (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="text-primary underline underline-offset-2"
      >
        {children}
      </a>
    ),
    img: ({ alt }) => <span className="text-fg-muted">{alt}</span>,
    table: ({ children }) => (
      <div className="my-4 overflow-x-auto">
        <table className="w-full border-collapse text-body-sm">{children}</table>
      </div>
    ),
    th: ({ children }) => (
      <th className="border border-border bg-user-bubble px-3 py-2 text-left text-h5">
        {children}
      </th>
    ),
    td: ({ children }) => <td className="border border-border px-3 py-2">{children}</td>,
    code: ({ children }) => (
      <code className="rounded bg-user-bubble px-1.5 py-0.5 font-mono text-code">{children}</code>
    ),
    pre: ({ children }) => {
      const child = Children.toArray(children)[0]
      if (!isValidElement<{ className?: string; children?: ReactNode }>(child)) {
        return <pre>{children}</pre>
      }
      return (
        <CodeBlock
          code={textOf(child.props.children).replace(/\n$/, '')}
          language={languageOf(child.props.className)}
          highlighting={!streaming}
        />
      )
    },
  }
}

export function Markdown({ text, streaming = false }: { text: string; streaming?: boolean }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components(streaming)}>
      {text}
    </ReactMarkdown>
  )
}
