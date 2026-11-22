import type { Message } from './api'

export type Intent = NonNullable<Message['intent']>
export type RouteSource = NonNullable<Message['route_source']>

/** What `route_selected` told us about the run that is streaming now. */
export type RouteInfo = { intent: Intent; source: RouteSource }

/**
 * The pipelines that differ from plain chat get a chip; a chat answer gets none (a chip on every
 * answer would be noise). A route that fell back to chat is a chat answer too.
 */
const LABELS: Partial<Record<Intent, string>> = {
  manage_memory: 'Memory',
  generate_document: 'Document',
  deep_research: 'Research',
}

export function modeLabel(intent: Message['intent'] | undefined): string | null {
  return (intent && LABELS[intent]) || null
}

/**
 * The same rule as the API (`runs.py::_regenerable`): only a chat answer can be regenerated. An
 * answer with no intent is a chat answer from before routing existed, unless it was a static reply
 * (`/help`, a usage message), which has no thread behind it.
 */
export function canRegenerate(message: Pick<Message, 'intent' | 'route_source'>): boolean {
  const intent = message.intent ?? null
  if (intent !== null && intent !== 'general_chat') return false
  return !(intent === null && message.route_source === 'command')
}

export type SlashCommand = {
  /** Shown in the menu. */
  name: string
  /** What the menu puts in the box; a command that takes text ends with a space. */
  insert: string
  hint: string
}

/** Doc 08 section 2.6. The menu only inserts text: the backend parses the command. */
export const COMMANDS: readonly SlashCommand[] = [
  { name: '/pdf', insert: '/pdf', hint: 'Turn this conversation into a PDF' },
  { name: '/pdf last', insert: '/pdf last', hint: 'Turn the last answer into a PDF' },
  { name: '/brief', insert: '/brief ', hint: 'Write a short PDF brief about a topic' },
  { name: '/research', insert: '/research ', hint: 'Research a question on the web' },
  { name: '/memory', insert: '/memory', hint: 'List what Nexus remembers about you' },
  { name: '/memory remember', insert: '/memory remember ', hint: 'Save something about you' },
  { name: '/memory forget', insert: '/memory forget ', hint: 'Remove a memory (asks first)' },
  { name: '/help', insert: '/help', hint: 'What Nexus can do' },
]

/**
 * The commands to offer for a draft. The menu is for the very first word only: the draft must be
 * `/` plus letters and nothing else, so `and/or`, a pasted path with a space, or a command that is
 * already followed by its text never opens it.
 */
export function matchCommands(draft: string): readonly SlashCommand[] {
  if (!/^\/\S*$/.test(draft)) return []
  const typed = draft.toLowerCase()
  return COMMANDS.filter((command) => command.name.startsWith(typed))
}

/** The id of one menu row; the composer points `aria-activedescendant` at it. */
export const optionId = (menuId: string, index: number) => `${menuId}-option-${index}`
