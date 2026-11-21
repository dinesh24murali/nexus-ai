import { describe, expect, it } from 'vitest'
import { highlight, languageFor } from './highlight'

describe('languageFor', () => {
  it('maps common names and aliases to a grammar, and ignores case', () => {
    expect(languageFor('python')).toBe('python')
    expect(languageFor('PY')).toBe('python')
    expect(languageFor('ts')).toBe('typescript')
    expect(languageFor('sh')).toBe('shellscript')
    expect(languageFor('bash')).toBe('shellscript')
  })

  it('returns null for an unknown or missing language', () => {
    expect(languageFor('brainfuck')).toBeNull()
    expect(languageFor(undefined)).toBeNull()
    expect(languageFor('')).toBeNull()
  })
})

describe('highlight', () => {
  it('splits code into lines of classified tokens that still add up to the original text', async () => {
    const code = 'def f(x):\n    return "a" + 1  # note'

    const lines = await highlight(code, 'python')

    expect(lines).not.toBeNull()
    expect(lines!.map((line) => line.map((t) => t.text).join('')).join('\n')).toBe(code)
    const all = lines!.flat()
    expect(all.find((t) => t.text === 'def')?.kind).toBe('keyword')
    expect(all.find((t) => t.text === '"a"')?.kind).toBe('string')
    expect(all.find((t) => t.text === '# note')?.kind).toBe('comment')
    expect(all.find((t) => t.text === '(')?.kind).toBe('plain')
  })

  it('returns null for a language without a grammar so the caller shows plain text', async () => {
    expect(await highlight('whatever', 'brainfuck')).toBeNull()
    expect(await highlight('whatever', undefined)).toBeNull()
  })

  it('never emits inline styles: tokens carry a kind, not a colour', async () => {
    const lines = await highlight('const a = 1', 'ts')
    for (const token of lines!.flat()) expect(Object.keys(token).sort()).toEqual(['kind', 'text'])
  })
})
