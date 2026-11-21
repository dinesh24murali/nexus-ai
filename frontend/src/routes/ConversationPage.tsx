import { Link, useParams } from 'react-router'
import { Alert } from '../components/ui/Alert'
import { useConversation } from '../features/conversations/api'
import { ApiProblem } from '../lib/auth'
import { describeProblem } from '../lib/messages'

/** One conversation. The message list and composer arrive in M2 (TASK-013). */
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

  return (
    <div className="mx-auto flex max-w-chat flex-col items-center px-4 py-24 text-center">
      <h1 className="text-h2 break-words">{data.title}</h1>
      <p className="mt-3 text-body-lg text-fg-muted">
        This conversation is empty. Chat is coming in the next milestone.
      </p>
    </div>
  )
}
