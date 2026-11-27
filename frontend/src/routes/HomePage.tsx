import { useState } from 'react'
import { useNavigate } from 'react-router'
import { Alert } from '../components/ui/Alert'
import { Composer } from '../features/chat/Composer'
import type { HandoffState } from '../features/chat/ChatView'
import { useCreateConversation } from '../features/conversations/api'
import { FilePicker } from '../features/files/AttachButton'
import { UploadChips } from '../features/files/UploadChips'
import { useAttachments } from '../features/files/uploads'
import { useAuth } from '../lib/auth-context'
import { describeProblem } from '../lib/messages'

/**
 * Empty state for a new chat. Sending creates the conversation, opens it, and hands the text over
 * so the conversation page sends it (the stream belongs to the page that shows it).
 */
export function HomePage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const create = useCreateConversation()
  const [error, setError] = useState<string | null>(null)
  const [text, setText] = useState('')
  const attachments = useAttachments()

  async function start(text: string): Promise<boolean> {
    setError(null)
    try {
      const created = await create.mutateAsync()
      const state: HandoffState = { initialMessage: text }
      void navigate(`/c/${created.id}`, { state })
      return true
    } catch (e) {
      setError(describeProblem(e).message)
      return false
    }
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-chat flex-col justify-center px-4">
      <h1 className="text-center text-h1">Hello, {user?.display_name}</h1>
      <p className="mt-3 mb-8 text-center text-body-lg text-fg-muted">What can I help with?</p>
      {error && (
        <Alert variant="error" className="mb-3">
          {error}
        </Alert>
      )}
      <UploadChips attachments={attachments} />
      <Composer
        attach={<FilePicker onPick={attachments.upload} />}
        text={text}
        setText={setText}
        onSend={start}
        onStop={() => undefined}
        running={false}
        stopping={false}
      />
    </div>
  )
}
