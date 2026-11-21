/**
 * The one hand-written API client: Server-Sent Events from the chat endpoints.
 *
 * Generated clients cannot express a stream (AGENTS.md section 3.7), so this file is allowed, with
 * two rules: payload types come from the generated `StreamEvent` schema, and the event names below
 * must stay in step with it (the compile-time check at the bottom fails when the spec gains one).
 *
 * `EventSource` is not used because it cannot POST or send an Authorization header. Instead this
 * reads a `fetch` body, so the bearer token and the one refresh-and-retry from `AuthManager.fetch`
 * apply as usual.
 */
import { parseProblem, type AuthManager } from '../lib/auth'
import type { components } from './schema'

export type StreamEvent = components['schemas']['StreamEvent']
export type StreamEventType = StreamEvent['type']

/** The stream closed or broke before a final event: the run may still be going on the server. */
export class StreamInterrupted extends Error {
  constructor(options?: ErrorOptions) {
    super('The stream ended before the run finished.', options)
    this.name = 'StreamInterrupted'
  }
}

/** The server sent something that is not valid for this protocol. */
export class StreamProtocolError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'StreamProtocolError'
  }
}

export type SseFrame = { event: string; data: string; id: string | undefined }

/**
 * Incremental parser for the `text/event-stream` format. Feed it text as it arrives, in any
 * chunking; it returns the frames completed so far. Comment lines (`: ping`) are dropped.
 */
export class SseParser {
  private buffer = ''
  private event = ''
  private data: string[] = []
  private id: string | undefined

  push(chunk: string): SseFrame[] {
    this.buffer += chunk
    const frames: SseFrame[] = []
    let start = 0
    for (let i = 0; i < this.buffer.length; i++) {
      const ch = this.buffer[i]
      if (ch !== '\n' && ch !== '\r') continue
      // A CR at the very end may be the first half of a CRLF: wait for the next chunk.
      if (ch === '\r' && i === this.buffer.length - 1) break
      const line = this.buffer.slice(start, i)
      if (ch === '\r' && this.buffer[i + 1] === '\n') i++
      start = i + 1
      const frame = this.line(line)
      if (frame) frames.push(frame)
    }
    this.buffer = this.buffer.slice(start)
    return frames
  }

  private line(line: string): SseFrame | null {
    if (line === '') {
      if (this.data.length === 0) {
        this.event = ''
        return null
      }
      const frame = { event: this.event || 'message', data: this.data.join('\n'), id: this.id }
      this.event = ''
      this.data = []
      return frame
    }
    if (line.startsWith(':')) return null
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') this.event = value
    else if (field === 'data') this.data.push(value)
    else if (field === 'id') this.id = value
    return null
  }
}

// Every event type in the spec. `satisfies` + the check below make a new spec event a type error.
const EVENT_TYPES = [
  'run_started',
  'route_selected',
  'progress',
  'document_created',
  'token',
  'tool_start',
  'tool_end',
  'citation',
  'interrupt',
  'memory_updated',
  'usage',
  'done',
  'error',
] as const satisfies readonly StreamEventType[]
type Unlisted = Exclude<StreamEventType, (typeof EVENT_TYPES)[number]>
const everySpecEventIsListed: [Unlisted] extends [never] ? true : never = true
void everySpecEventIsListed

const KNOWN = new Set<string>(EVENT_TYPES)
/** After one of these the server ends the stream: there is nothing more to read. */
const TERMINAL = new Set<string>(['done', 'error', 'interrupt'])

function abortError(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException('The operation was aborted.', 'AbortError')
}

/** Read events from a response body until a final event (`done`, `error` or `interrupt`). */
export async function* readEvents(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  const parser = new SseParser()
  const onAbort = () => void reader.cancel(abortError(signal!)).catch(() => undefined)
  signal?.addEventListener('abort', onAbort)
  try {
    for (;;) {
      if (signal?.aborted) throw abortError(signal)
      let chunk: ReadableStreamReadResult<Uint8Array>
      try {
        chunk = await reader.read()
      } catch (cause) {
        if (signal?.aborted) throw abortError(signal)
        throw new StreamInterrupted({ cause })
      }
      if (signal?.aborted) throw abortError(signal)
      if (chunk.done) throw new StreamInterrupted()
      for (const frame of parser.push(decoder.decode(chunk.value, { stream: true }))) {
        let payload: unknown
        try {
          payload = JSON.parse(frame.data)
        } catch {
          throw new StreamProtocolError('A stream frame was not valid JSON.')
        }
        const type = (payload as { type?: unknown } | null)?.type
        if (typeof type !== 'string' || !KNOWN.has(type)) continue // newer server: skip
        yield payload as StreamEvent
        if (TERMINAL.has(type)) return
      }
    }
  } finally {
    signal?.removeEventListener('abort', onAbort)
    await reader.cancel().catch(() => undefined)
  }
}

const API_BASE = () => `${globalThis.location.origin}/api/v1`

/**
 * POST to a streaming endpoint and yield its events. A refusal before the stream opens (budget,
 * rate limit, moderation, 409, 503) throws the usual `ApiProblem`.
 */
export async function* openStream(
  manager: Pick<AuthManager, 'fetch'>,
  path: string,
  options: { body?: unknown; signal?: AbortSignal },
): AsyncGenerator<StreamEvent> {
  const headers: Record<string, string> = { accept: 'text/event-stream' }
  if (options.body !== undefined) headers['content-type'] = 'application/json'
  const response = await manager.fetch(`${API_BASE()}${path}`, {
    method: 'POST',
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
  })
  if (!response.ok) throw await parseProblem(response)
  if (!response.body) throw new StreamInterrupted()
  yield* readEvents(response.body, options.signal)
}
