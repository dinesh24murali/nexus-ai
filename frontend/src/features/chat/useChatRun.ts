import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useApiClient } from '../../api/client'
import { openStream } from '../../api/stream'
import { useAuthManager } from '../../lib/auth-context'
import { useConversationKeys } from '../conversations/api'
import type { Conversation } from '../conversations/api'
import { useChatKeys } from './api'
import type { Decision, PendingInterrupt } from './ApprovalCard'
import { useMemoryWatch } from '../settings/useMemoryWatch'
import { noticeFromError, type Notice } from './notices'
import { TITLE_REFRESH_DELAYS_MS, TOKEN_BATCH_MS } from './timing'
import type { ToolCallView } from './toolCalls'

export type ChatRun = {
  running: boolean
  stopping: boolean
  /** The text just sent, shown until the stored message replaces it. */
  pendingUser: string | null
  streamingText: string
  /** Ids the server announced in `run_started`; once a stored message has the id, it replaces the live one. */
  userMessageId: string | null
  assistantMessageId: string | null
  /** The stored answer a regenerate will replace (hidden while the new one streams). */
  replacesMessageId: string | null
  /** The tool calls of this run so far, in the order they started. */
  toolCalls: ToolCallView[]
  notice: Notice | null
}

const IDLE: ChatRun = {
  running: false,
  stopping: false,
  pendingUser: null,
  streamingText: '',
  userMessageId: null,
  assistantMessageId: null,
  replacesMessageId: null,
  toolCalls: [],
  notice: null,
}

const DEFAULT_TITLE = 'New chat'

/** Add a call, or replace the one with the same id (a start seen twice shows once). */
function withCall(calls: ToolCallView[], call: ToolCallView): ToolCallView[] {
  return calls.some((c) => c.id === call.id)
    ? calls.map((c) => (c.id === call.id ? call : c))
    : [...calls, call]
}

type ToolEnd = { call_id: string; name: string; status: ToolCallView['status']; summary?: string }

/** A call that ended. If its start was never seen, it still shows (with no arguments). */
function settleCall(calls: ToolCallView[], end: ToolEnd): ToolCallView[] {
  const settled = { status: end.status, summary: end.summary }
  return calls.some((c) => c.id === end.call_id)
    ? calls.map((c) => (c.id === end.call_id ? { ...c, ...settled } : c))
    : [...calls, { id: end.call_id, name: end.name, args: {}, ...settled }]
}

type Live = { controller: AbortController; text: string; timer: number | undefined }

/**
 * One conversation's run: send, stop and regenerate, and what the screen shows meanwhile.
 *
 * The run belongs to the server (ADR-0006): closing this stream does not stop it, so "Stop" asks
 * the API to cancel, and leaving the page only closes the connection. After a run the stored
 * messages are re-read, so what you see afterwards is what was saved.
 */
export function useChatRun(conversationId: string) {
  const manager = useAuthManager()
  const api = useApiClient()
  const queryClient = useQueryClient()
  const chatKeys = useChatKeys()
  const conversationKeys = useConversationKeys()
  const [state, setState] = useState<ChatRun>(IDLE)
  const live = useRef<Live | null>(null)
  const mounted = useRef(false)
  const titleTimers = useRef<number[]>([])
  const memoryWatch = useMemoryWatch()

  useEffect(() => {
    mounted.current = true
    const timers = titleTimers.current
    return () => {
      mounted.current = false
      // StrictMode runs cleanup and setup back to back on the same instance: only close the
      // stream (and stop the timers) if the component really did go away.
      setTimeout(() => {
        if (mounted.current) return
        live.current?.controller.abort()
        timers.forEach(clearTimeout)
      }, 0)
    }
  }, [])

  const refreshTitle = useCallback(() => {
    titleTimers.current.forEach(clearTimeout)
    titleTimers.current = TITLE_REFRESH_DELAYS_MS.map((delay) =>
      window.setTimeout(() => {
        const cached = queryClient.getQueryData<Conversation>(conversationKeys.one(conversationId))
        if (cached && cached.title !== DEFAULT_TITLE) return
        void queryClient.invalidateQueries({ queryKey: conversationKeys.one(conversationId) })
        void queryClient.invalidateQueries({ queryKey: conversationKeys.list })
      }, delay),
    )
  }, [queryClient, conversationKeys, conversationId])

  /** Resolves true once the server accepted the run (its first event), false if it refused. */
  const start = useCallback(
    (
      path: string,
      body: unknown,
      options: {
        pendingUser: string | null
        replacesMessageId: string | null
        /** The turn can teach Nexus something (a send, or the resume that ends a turn). */
        remembers?: boolean
      },
    ): Promise<boolean> => {
      if (live.current) return Promise.resolve(false)
      const controller = new AbortController()
      const run: Live = { controller, text: '', timer: undefined }
      live.current = run
      if (options.remembers) memoryWatch.begin()
      setState({
        ...IDLE,
        running: true,
        pendingUser: options.pendingUser,
        replacesMessageId: options.replacesMessageId,
      })

      let accept: (ok: boolean) => void = () => undefined
      const accepted = new Promise<boolean>((resolve) => {
        accept = resolve
      })

      const flush = () => {
        run.timer = undefined
        setState((s) => ({ ...s, streamingText: run.text }))
      }

      void (async () => {
        let notice: Notice | null = null
        let started = false
        try {
          for await (const event of openStream(manager, path, {
            body,
            signal: controller.signal,
          })) {
            started = true
            accept(true)
            if (event.type === 'run_started') {
              setState((s) => ({
                ...s,
                userMessageId: event.user_message_id,
                assistantMessageId: event.assistant_message_id,
              }))
            } else if (event.type === 'token') {
              run.text += event.delta
              run.timer ??= window.setTimeout(flush, TOKEN_BATCH_MS)
            } else if (event.type === 'tool_start') {
              setState((s) => ({
                ...s,
                toolCalls: withCall(s.toolCalls, {
                  id: event.call_id,
                  name: event.name,
                  args: event.args,
                  status: 'running',
                }),
              }))
            } else if (event.type === 'tool_end') {
              setState((s) => ({
                ...s,
                toolCalls: settleCall(s.toolCalls, event),
              }))
            } else if (event.type === 'error') {
              notice = { kind: 'run_error', message: event.message, retryable: event.retryable }
            }
          }
        } catch (error) {
          if (!controller.signal.aborted) notice = noticeFromError(error)
        } finally {
          accept(false) // no effect once accepted
          if (run.timer !== undefined) clearTimeout(run.timer)
          if (!controller.signal.aborted) {
            // Show everything that arrived (the last batch may still be waiting for its timer).
            setState((s) => ({ ...s, streamingText: run.text }))
            if (started) {
              // Re-read what the server saved before dropping the live view, so nothing flickers.
              await Promise.allSettled([
                queryClient.invalidateQueries({ queryKey: chatKeys.messages(conversationId) }),
                queryClient.invalidateQueries({ queryKey: chatKeys.usage }),
                queryClient.invalidateQueries({ queryKey: conversationKeys.list }),
                // A run that stopped at an approval parked it on the conversation: this is how
                // the card appears (and how a stale one goes away).
                queryClient.invalidateQueries({ queryKey: conversationKeys.one(conversationId) }),
              ])
              refreshTitle()
              if (options.remembers && !notice) memoryWatch.finish()
            } else {
              // Refused before it started (say, the approval was answered elsewhere): re-read
              // what the server holds so the screen does not keep a stale card.
              await queryClient
                .invalidateQueries({ queryKey: conversationKeys.one(conversationId) })
                .catch(() => undefined)
            }
            live.current = null
            setState({ ...IDLE, notice })
          } else {
            live.current = null
          }
        }
      })()
      return accepted
    },
    [manager, queryClient, chatKeys, conversationKeys, conversationId, refreshTitle, memoryWatch],
  )

  const send = useCallback(
    (text: string) =>
      start(
        `/conversations/${conversationId}/messages`,
        { content: text },
        { pendingUser: text, replacesMessageId: null, remembers: true },
      ),
    [start, conversationId],
  )

  const regenerate = useCallback(
    (replacesMessageId: string | null) =>
      start(`/conversations/${conversationId}/regenerate`, undefined, {
        pendingUser: null,
        replacesMessageId,
      }),
    [start, conversationId],
  )

  /** Answer the pending approval; the resumed run streams like any other. */
  const decide = useCallback(
    (interrupt: PendingInterrupt, decision: Decision) =>
      start(
        `/conversations/${conversationId}/runs/resume`,
        {
          interrupt_id: interrupt.interrupt_id,
          decision: decision.decision,
          ...(decision.decision === 'edit' ? { edited_args: { query: decision.query } } : {}),
        },
        { pendingUser: null, replacesMessageId: null, remembers: true },
      ),
    [start, conversationId],
  )

  const stop = useCallback(async () => {
    if (!live.current) return
    setState((s) => ({ ...s, stopping: true }))
    try {
      await api.POST('/conversations/{conversationId}/runs/cancel', {
        params: { path: { conversationId } },
      })
    } catch {
      // The stream still ends when the run does; a failed cancel just means it keeps going.
    }
  }, [api, conversationId])

  const reload = useCallback(async () => {
    setState((s) => ({ ...s, notice: null }))
    await Promise.allSettled([
      queryClient.invalidateQueries({ queryKey: chatKeys.messages(conversationId) }),
      queryClient.invalidateQueries({ queryKey: conversationKeys.one(conversationId) }),
      queryClient.invalidateQueries({ queryKey: chatKeys.usage }),
    ])
  }, [queryClient, chatKeys, conversationKeys, conversationId])

  const dismissNotice = useCallback(() => setState((s) => ({ ...s, notice: null })), [])

  return { state, send, regenerate, decide, stop, reload, dismissNotice }
}
