import { Link, useParams } from 'react-router'
import { Alert } from '../components/ui/Alert'
import { ChatView } from '../features/chat/ChatView'
import { useConversation } from '../features/conversations/api'
import { ApiProblem } from '../lib/auth'
import { describeProblem } from '../lib/messages'

/** One conversation: loads it, then hands over to the chat view (history, answer, composer). */
export function ConversationPage() {
  const { conversationId } = useParams()
  const { data, error, isPending } = useConversation(conversationId)

  if (isPending) {
    return <div role="status" aria-label="Loading conversation" className="p-8" />
  }

  if (error) {
    const missing = error instanceof ApiProblem && error.status === 404
    return (
      <div className="mx-auto flex max-w-chat flex-col items-center px-4 py-24 text-center">
        {missing ? (
          <>
            <h1 className="text-h2">Conversation not found</h1>
            <p className="mt-2 text-fg-muted">It may have been deleted, or it never existed.</p>
            <Link to="/" className="mt-6 text-primary underline underline-offset-2">
              Start a new chat
            </Link>
          </>
        ) : (
          <Alert variant="error">{describeProblem(error).message}</Alert>
        )}
      </div>
    )
  }

  // Keyed by id so switching conversations starts with a clean run state.
  return <ChatView key={data.id} conversation={data} />
}
