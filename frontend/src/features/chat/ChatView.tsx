import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { Alert } from '../../components/ui/Alert'
import { Button } from '../../components/ui/Button'
import { describeProblem } from '../../lib/messages'
import type { Conversation } from '../conversations/api'
import { FilePicker } from '../files/AttachButton'
import { UploadChips } from '../files/UploadChips'
import { useAttachments } from '../files/uploads'
import { chronological, useMessages } from './api'
import { Composer } from './Composer'
import { MessageList } from './MessageList'
import { RunNotice } from './RunNotice'
import { useChatRun } from './useChatRun'

const STICK_THRESHOLD_PX = 80

/** The home page hands the first message over in router state; see HomePage. */
export type HandoffState = { initialMessage?: string } | null

/** One conversation: history, the live answer, and the composer. */
export function ChatView({ conversation }: { conversation: Conversation }) {
  const history = useMessages(conversation.id)
  const chat = useChatRun(conversation.id)
  const location = useLocation()
  const navigate = useNavigate()
  const scroller = useRef<HTMLDivElement>(null)
  const stickToBottom = useRef(true)
  const handedOff = useRef(false)
  const [text, setText] = useState('')
  const attachments = useAttachments()
  const { send } = chat

  const messages = chronological(history.data)
  const latest = messages.at(-1)
  const lastAssistantId = latest?.role === 'assistant' ? latest.id : null
  const { running, streamingText, pendingUser } = chat.state

  // Send the message typed on the home page, once. The state is cleared right away so a reload
  // does not send it again; the ref covers StrictMode running this effect twice in development.
  useEffect(() => {
    const initial = (location.state as HandoffState)?.initialMessage
    if (!initial || handedOff.current) return
    handedOff.current = true
    void navigate(location.pathname, { replace: true, state: null })
    void send(initial).then((accepted) => {
      // Refused (moderation, a limit, ...): the home page's box is gone, so put the text back here.
      if (!accepted) setText((current) => (current === '' ? initial : current))
    })
  }, [location.state, location.pathname, navigate, send])

  // Follow the answer as it grows, unless the reader scrolled up to read something.
  useLayoutEffect(() => {
    const el = scroller.current
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight
  }, [messages.length, streamingText, pendingUser, running])

  function onScroll() {
    const el = scroller.current
    if (!el) return
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD_PX
  }

  function sendAndFollow(text: string) {
    stickToBottom.current = true
    return chat.send(text)
  }

  return (
    <div className="flex h-screen flex-col">
      <header className="border-b border-border px-6 py-3">
        <h1 className="truncate text-h5">{conversation.title}</h1>
      </header>

      <div ref={scroller} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-chat px-4 py-6">
          {history.isPending ? (
            <div role="status" aria-label="Loading messages" className="py-12" />
          ) : history.isError ? (
            <Alert variant="error">
              <p>{describeProblem(history.error).message}</p>
              <Button
                size="sm"
                variant="secondary"
                className="mt-2"
                onClick={() => history.refetch()}
              >
                Try again
              </Button>
            </Alert>
          ) : messages.length === 0 && !running ? (
            <p className="py-16 text-center text-body-lg text-fg-muted">
              Ask anything to get started.
            </p>
          ) : (
            <MessageList
              messages={messages}
              run={chat.state}
              hasEarlier={history.hasNextPage}
              loadingEarlier={history.isFetchingNextPage}
              onLoadEarlier={() => void history.fetchNextPage()}
              onRegenerate={() => void chat.regenerate(lastAssistantId)}
            />
          )}
        </div>
      </div>

      <div className="mx-auto w-full max-w-chat px-4 pb-4">
        {chat.state.notice && (
          <div className="mb-3">
            <RunNotice
              notice={chat.state.notice}
              onReload={() => void chat.reload()}
              onDismiss={chat.dismissNotice}
            />
          </div>
        )}
        <UploadChips attachments={attachments} />
        <Composer
          attach={<FilePicker onPick={attachments.upload} />}
          text={text}
          setText={setText}
          onSend={sendAndFollow}
          onStop={() => void chat.stop()}
          running={running}
          stopping={chat.state.stopping}
        />
      </div>
    </div>
  )
}
