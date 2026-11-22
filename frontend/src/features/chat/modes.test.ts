import { describe, expect, it } from 'vitest'
import { canRegenerate, COMMANDS, matchCommands, modeLabel } from './modes'

describe('modeLabel', () => {
  it('names the pipelines that differ from chat', () => {
    expect(modeLabel('manage_memory')).toBe('Memory')
    expect(modeLabel('generate_document')).toBe('Document')
    expect(modeLabel('deep_research')).toBe('Research')
  })

  it('gives a chat answer, an old answer and a missing intent no chip', () => {
    expect(modeLabel('general_chat')).toBeNull()
    expect(modeLabel(null)).toBeNull()
    expect(modeLabel(undefined)).toBeNull()
  })
})

describe('canRegenerate (the API rule, so the button never leads to a 409)', () => {
  it.each([
    ['a chat answer', { intent: 'general_chat', route_source: 'router' }, true],
    [
      'a chat answer after a router fallback',
      { intent: 'general_chat', route_source: 'fallback' },
      true,
    ],
    ['a chat answer from before routing', { intent: null, route_source: null }, true],
    ['a chat answer with no route fields at all', {}, true],
    ['a memory answer', { intent: 'manage_memory', route_source: 'command' }, false],
    ['a document answer', { intent: 'generate_document', route_source: 'router' }, false],
    ['a research answer', { intent: 'deep_research', route_source: 'router' }, false],
    ['a /help or usage reply', { intent: null, route_source: 'command' }, false],
  ] as const)('%s', (_name, fields, expected) => {
    expect(canRegenerate(fields as Parameters<typeof canRegenerate>[0])).toBe(expected)
  })
})

describe('matchCommands', () => {
  it('lists every command for a bare slash', () => {
    expect(matchCommands('/')).toEqual(COMMANDS)
  })

  it('filters by what was typed, in any letter case', () => {
    expect(matchCommands('/pd').map((c) => c.name)).toEqual(['/pdf', '/pdf last'])
    expect(matchCommands('/ME').map((c) => c.name)).toEqual([
      '/memory',
      '/memory remember',
      '/memory forget',
    ])
  })

  it('stays closed for anything that is not a lone first word', () => {
    expect(matchCommands('')).toEqual([])
    expect(matchCommands('hello /pdf')).toEqual([])
    expect(matchCommands('and/or')).toEqual([])
    expect(matchCommands('/pdf last')).toEqual([]) // the command is complete and has a space
    expect(matchCommands('/brief rust')).toEqual([])
    expect(matchCommands('/etc/hosts')).toEqual([]) // no command starts with it
    expect(matchCommands('/nope')).toEqual([])
  })

  it('inserts a trailing space only where the command takes text', () => {
    const insert = Object.fromEntries(COMMANDS.map((c) => [c.name, c.insert]))
    expect(insert['/pdf']).toBe('/pdf')
    expect(insert['/pdf last']).toBe('/pdf last')
    expect(insert['/help']).toBe('/help')
    expect(insert['/brief']).toBe('/brief ')
    expect(insert['/research']).toBe('/research ')
    expect(insert['/memory remember']).toBe('/memory remember ')
  })
})
