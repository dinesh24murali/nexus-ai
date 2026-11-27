import type { components } from '../../api/schema'

type ApiToolCall = components['schemas']['ToolCall']

/** `running` exists only on the screen: the API reports a call once it has finished. */
export type ToolStatus = 'running' | ApiToolCall['status']

export type ToolCallView = {
  id: string
  name: string
  args: Record<string, unknown>
  status: ToolStatus
  summary?: string | null
}

/** A stored call in the shape the screen uses. */
export const toView = (call: ApiToolCall): ToolCallView => ({ ...call })

type Words = { running: string; completed: string; failed: string; timed_out: string }

export const KNOWN: Record<string, { words: Words; headline: string; declined: string }> = {
  search_my_files: {
    words: {
      running: 'Searching your files',
      completed: 'Searched your files',
      failed: 'File search failed',
      timed_out: 'File search timed out',
    },
    headline: 'query',
    declined: 'File search declined',
  },
  web_search: {
    words: {
      running: 'Searching the web',
      completed: 'Searched the web',
      failed: 'Web search failed',
      timed_out: 'Web search timed out',
    },
    headline: 'query',
    declined: 'Web search declined',
  },
  calculator: {
    words: {
      running: 'Calculating',
      completed: 'Calculated',
      failed: 'Calculation failed',
      timed_out: 'Calculation timed out',
    },
    headline: 'expression',
    declined: 'Calculation declined',
  },
}

/** The words for a call. A tool this build does not know is shown by its own name. */
export function labelOf(call: Pick<ToolCallView, 'name' | 'status'>): string {
  const known = KNOWN[call.name]
  if (!known) {
    const suffix = {
      running: 'running',
      completed: 'done',
      failed: 'failed',
      rejected: 'declined',
      timed_out: 'timed out',
    }[call.status]
    return `${call.name}: ${suffix}`
  }
  return call.status === 'rejected' ? known.declined : known.words[call.status]
}

/** One short line per argument, as text. Values the model controls are never markup. */
export function argLines(call: ToolCallView): { key: string; value: string }[] {
  const headline = KNOWN[call.name]?.headline
  const lines: { key: string; value: string }[] = []
  for (const [key, value] of Object.entries(call.args)) {
    if (key === headline && typeof value === 'string') continue
    if (key === 'file_ids' && Array.isArray(value)) {
      lines.push({
        key: 'in',
        value: `${value.length} selected file${value.length === 1 ? '' : 's'}`,
      })
      continue
    }
    lines.push({ key, value: typeof value === 'string' ? value : JSON.stringify(value) })
  }
  return lines
}
