/**
 * Syntax highlighting for code blocks with Shiki.
 *
 * Shiki's stock HTML output sets `style="color: ..."` on every token, and the CSP forbids inline
 * styles. So this asks for tokens (with the `css-variables` theme, whose colours are variable
 * names) and reduces each to a `kind`; the component maps a kind to a Tailwind class built from
 * the design tokens. Grammars are separate lazily loaded chunks, so the first answer without code
 * pays nothing, and the regex engine is the pure-JS one (no WASM, which the CSP would also block).
 */
import type { HighlighterCore } from 'shiki/core'

export type TokenKind = 'plain' | 'keyword' | 'string' | 'comment'
export type Token = { text: string; kind: TokenKind }

const GRAMMARS = {
  bash: () => import('shiki/langs/shellscript.mjs'),
  c: () => import('shiki/langs/c.mjs'),
  cpp: () => import('shiki/langs/cpp.mjs'),
  css: () => import('shiki/langs/css.mjs'),
  diff: () => import('shiki/langs/diff.mjs'),
  go: () => import('shiki/langs/go.mjs'),
  html: () => import('shiki/langs/html.mjs'),
  java: () => import('shiki/langs/java.mjs'),
  javascript: () => import('shiki/langs/javascript.mjs'),
  json: () => import('shiki/langs/json.mjs'),
  jsx: () => import('shiki/langs/jsx.mjs'),
  markdown: () => import('shiki/langs/markdown.mjs'),
  python: () => import('shiki/langs/python.mjs'),
  rust: () => import('shiki/langs/rust.mjs'),
  sql: () => import('shiki/langs/sql.mjs'),
  tsx: () => import('shiki/langs/tsx.mjs'),
  typescript: () => import('shiki/langs/typescript.mjs'),
  yaml: () => import('shiki/langs/yaml.mjs'),
} as const

type Grammar = keyof typeof GRAMMARS

const ALIASES: Record<string, Grammar> = {
  sh: 'bash',
  shell: 'bash',
  shellscript: 'bash',
  zsh: 'bash',
  py: 'python',
  python3: 'python',
  js: 'javascript',
  mjs: 'javascript',
  ts: 'typescript',
  yml: 'yaml',
  md: 'markdown',
  rs: 'rust',
  golang: 'go',
  'c++': 'cpp',
  htm: 'html',
}

/** The Shiki grammar name for a fenced-code language tag, or null when there is none. */
export function languageFor(tag: string | undefined): string | null {
  const key = tag?.trim().toLowerCase()
  if (!key) return null
  const grammar = key in GRAMMARS ? (key as Grammar) : ALIASES[key]
  if (!grammar) return null
  return grammar === 'bash' ? 'shellscript' : grammar
}

let highlighter: Promise<HighlighterCore> | undefined
const loaded = new Set<string>()

function core(): Promise<HighlighterCore> {
  highlighter ??= (async () => {
    const [{ createHighlighterCore, createCssVariablesTheme }, { createJavaScriptRegexEngine }] =
      await Promise.all([import('shiki/core'), import('shiki/engine/javascript')])
    return createHighlighterCore({
      themes: [createCssVariablesTheme({ name: 'css-variables', variablePrefix: '--shiki-' })],
      langs: [],
      engine: createJavaScriptRegexEngine(),
    })
  })()
  return highlighter
}

function kindOf(color: string | undefined): TokenKind {
  if (!color) return 'plain'
  if (color.includes('token-keyword')) return 'keyword'
  if (color.includes('token-string') || color.includes('token-constant')) return 'string'
  if (color.includes('token-comment')) return 'comment'
  return 'plain'
}

/** Tokens per line, or null when the language has no grammar (show the code as plain text). */
export async function highlight(code: string, tag: string | undefined): Promise<Token[][] | null> {
  const language = languageFor(tag)
  if (!language) return null
  const name = (language === 'shellscript' ? 'bash' : language) as Grammar
  const shiki = await core()
  if (!loaded.has(language)) {
    const grammar = await GRAMMARS[name]()
    await shiki.loadLanguage(grammar.default)
    loaded.add(language)
  }
  const { tokens } = shiki.codeToTokens(code, { lang: language, theme: 'css-variables' })
  return tokens.map((line) => line.map((t) => ({ text: t.content, kind: kindOf(t.color) })))
}
