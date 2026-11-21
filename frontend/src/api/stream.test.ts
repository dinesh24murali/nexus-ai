import { describe, expect, it, vi } from 'vitest'
import { ApiProblem } from '../lib/auth'
import fixture from './fixtures/send-stream.txt?raw'
import {
  openStream,
  readEvents,
  SseParser,
  StreamInterrupted,
  StreamProtocolError,
  type StreamEvent,
} from './stream'

const encoder = new TextEncoder()

/** A body that delivers `chunks` one by one, then closes (or fails with `failWith`). */
function bodyOf(chunks: (string | Uint8Array)[], failWith?: unknown): ReadableStream<Uint8Array> {
  let i = 0
  return new ReadableStream({
    pull(controller) {
      if (i < chunks.length) {
        const chunk = chunks[i++]!
        controller.enqueue(typeof chunk === 'string' ? encoder.encode(chunk) : chunk)
      } else if (failWith !== undefined) {
        controller.error(failWith)
      } else {
        controller.close()
      }
    },
  })
}

async function collect(source: AsyncIterable<StreamEvent>): Promise<StreamEvent[]> {
  const out: StreamEvent[] = []
  for await (const event of source) out.push(event)
  return out
}

/** The recorded stream cut into pieces of `size` characters. */
function pieces(text: string, size: number): string[] {
  const out: string[] = []
  for (let i = 0; i < text.length; i += size) out.push(text.slice(i, i + size))
  return out
}

const FRAME = 'event: token\nid: 2\ndata: {"type":"token","delta":"hi"}\n\n'

describe('SseParser', () => {
  it('turns a complete frame into an event, a data string and an id', () => {
    const frames = new SseParser().push(FRAME)
    expect(frames).toEqual([{ event: 'token', id: '2', data: '{"type":"token","delta":"hi"}' }])
  })

  it('gives the same frames however the text is cut into chunks', () => {
    const whole = new SseParser().push(fixture)
    expect(whole).toHaveLength(5)
    for (let size = 1; size <= 40; size++) {
      const parser = new SseParser()
      const frames = pieces(fixture, size).flatMap((piece) => parser.push(piece))
      expect(frames, `chunk size ${size}`).toEqual(whole)
    }
  })

  it('ignores ping comments, even between frames', () => {
    const frames = new SseParser().push(`: ping\n\n${FRAME}: ping\n\n${FRAME}`)
    expect(frames).toHaveLength(2)
  })

  it('accepts CRLF and bare CR line endings', () => {
    const crlf = new SseParser().push(FRAME.replaceAll('\n', '\r\n'))
    // A CR at the very end of the input is held back (it may be half of a CRLF), so more follows.
    const cr = new SseParser().push(FRAME.replaceAll('\n', '\r') + ': next\r')
    expect(crlf).toHaveLength(1)
    expect(cr).toEqual(crlf)
  })

  it('does not break a CRLF that is split between two chunks', () => {
    const parser = new SseParser()
    const text = FRAME.replaceAll('\n', '\r\n')
    const cut = text.indexOf('\r\n') + 1 // between the CR and the LF of the first line
    const frames = [...parser.push(text.slice(0, cut)), ...parser.push(text.slice(cut))]
    expect(frames).toEqual([{ event: 'token', id: '2', data: '{"type":"token","delta":"hi"}' }])
  })

  it('joins several data lines with a newline', () => {
    const frames = new SseParser().push('event: x\ndata: one\ndata: two\n\n')
    expect(frames[0]!.data).toBe('one\ntwo')
  })

  it('accepts a field with no space after the colon and ignores unknown fields', () => {
    const frames = new SseParser().push('retry: 5000\nevent:x\ndata:{"a":1}\n\n')
    expect(frames).toEqual([{ event: 'x', id: undefined, data: '{"a":1}' }])
  })

  it('drops a frame that never got its blank line', () => {
    expect(new SseParser().push('event: token\ndata: {"type":"token"}\n')).toEqual([])
  })
})

describe('readEvents', () => {
  it('yields the typed events of a recorded stream and stops at done', async () => {
    const events = await collect(readEvents(bodyOf([fixture])))
    expect(events.map((e) => e.type)).toEqual(['run_started', 'token', 'token', 'usage', 'done'])
    const text = events.flatMap((e) => (e.type === 'token' ? [e.delta] : [])).join('')
    expect(text).toBe('Héllo wörld 🌍')
  })

  it.each([1, 2, 3, 7, 64, 10_000])('is chunk-size independent (%i bytes)', async (size) => {
    const bytes = encoder.encode(fixture)
    const chunks: Uint8Array[] = []
    for (let i = 0; i < bytes.length; i += size) chunks.push(bytes.slice(i, i + size))
    const events = await collect(readEvents(bodyOf(chunks)))
    const text = events.flatMap((e) => (e.type === 'token' ? [e.delta] : [])).join('')
    expect(text).toBe('Héllo wörld 🌍') // multi-byte characters split across chunks survive
    expect(events.at(-1)?.type).toBe('done')
  })

  it('ends after an error event, which is delivered to the caller', async () => {
    const error =
      'event: error\ndata: {"type":"error","code":"LLM_UNAVAILABLE","message":"down","retryable":true}\n\n'
    const events = await collect(readEvents(bodyOf([FRAME, error, FRAME])))
    expect(events.map((e) => e.type)).toEqual(['token', 'error'])
  })

  it('ends after an interrupt event', async () => {
    const interrupt =
      'event: interrupt\ndata: {"type":"interrupt","interrupt":{"interrupt_id":"i1","tool":"web_search","args":{},"created_at":"2026-11-21T10:00:00Z","expires_at":"2026-11-22T10:00:00Z"}}\n\n'
    const events = await collect(readEvents(bodyOf([interrupt, FRAME])))
    expect(events.map((e) => e.type)).toEqual(['interrupt'])
  })

  it('reports a stream that closes without a final event as interrupted', async () => {
    await expect(collect(readEvents(bodyOf([FRAME])))).rejects.toBeInstanceOf(StreamInterrupted)
  })

  it('reports a network failure mid-stream as interrupted, keeping what arrived', async () => {
    const seen: StreamEvent[] = []
    const run = async () => {
      for await (const event of readEvents(bodyOf([FRAME], new TypeError('network error')))) {
        seen.push(event)
      }
    }
    await expect(run()).rejects.toBeInstanceOf(StreamInterrupted)
    expect(seen.map((e) => e.type)).toEqual(['token'])
  })

  it('rejects a frame whose data is not JSON', async () => {
    await expect(
      collect(readEvents(bodyOf(['event: token\ndata: {oops\n\n']))),
    ).rejects.toBeInstanceOf(StreamProtocolError)
  })

  it('ignores event types it does not know, so a newer server does not break an older client', async () => {
    const future = 'event: shiny_new\ndata: {"type":"shiny_new","x":1}\n\n'
    const done =
      'event: done\ndata: {"type":"done","message_id":"5b6c7d8e-1a2b-4c3d-9e8f-0a1b2c3d4e5f","finish_reason":"stop"}\n\n'
    const events = await collect(readEvents(bodyOf([future, FRAME, done])))
    expect(events.map((e) => e.type)).toEqual(['token', 'done'])
  })

  it('stops reading when the caller aborts', async () => {
    const controller = new AbortController()
    const cancelled = vi.fn()
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(encoder.encode(FRAME))
      },
      cancel: cancelled,
    })
    const seen: string[] = []
    const run = async () => {
      for await (const event of readEvents(body, controller.signal)) {
        seen.push(event.type)
        controller.abort()
      }
    }

    await expect(run()).rejects.toMatchObject({ name: 'AbortError' })
    expect(seen).toEqual(['token'])
    expect(cancelled).toHaveBeenCalled()
  })
})

describe('openStream', () => {
  const okResponse = (body: ReadableStream<Uint8Array> | null) =>
    new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } })

  it('posts the JSON body through the authenticated fetch and yields the events', async () => {
    const fetch = vi.fn(async () => okResponse(bodyOf([fixture])))
    const signal = new AbortController().signal

    const events = await collect(
      openStream({ fetch }, '/conversations/c1/messages', { body: { content: 'hi' }, signal }),
    )

    expect(events.at(-1)?.type).toBe('done')
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe(`${globalThis.location.origin}/api/v1/conversations/c1/messages`)
    expect(init.method).toBe('POST')
    expect(init.body).toBe('{"content":"hi"}')
    expect(init.signal).toBe(signal)
    const headers = new Headers(init.headers)
    expect(headers.get('content-type')).toBe('application/json')
    expect(headers.get('accept')).toBe('text/event-stream')
  })

  it('sends no body and no content type when there is nothing to send (regenerate)', async () => {
    const fetch = vi.fn(async () => okResponse(bodyOf([fixture])))

    await collect(openStream({ fetch }, '/conversations/c1/regenerate', {}))

    const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(init.body).toBeUndefined()
    expect(new Headers(init.headers).has('content-type')).toBe(false)
  })

  it('turns a refusal before the stream into the API error, with Retry-After', async () => {
    const refusal = new Response(
      JSON.stringify({
        error: {
          code: 'BUDGET_EXCEEDED',
          message: 'limit',
          details: { scope: 'global', resets_at: '2026-11-22T00:00:00Z' },
          request_id: 'req_1',
        },
      }),
      { status: 429, headers: { 'content-type': 'application/json', 'retry-after': '3600' } },
    )
    const fetch = vi.fn(async () => refusal)

    const error = await collect(openStream({ fetch }, '/x', {})).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiProblem)
    expect(error).toMatchObject({
      status: 429,
      code: 'BUDGET_EXCEEDED',
      retryAfterS: 3600,
      details: { scope: 'global' },
    })
  })

  it('treats a 200 with no body as an interrupted stream', async () => {
    const fetch = vi.fn(async () => okResponse(null))
    await expect(collect(openStream({ fetch }, '/x', {}))).rejects.toBeInstanceOf(StreamInterrupted)
  })
})
