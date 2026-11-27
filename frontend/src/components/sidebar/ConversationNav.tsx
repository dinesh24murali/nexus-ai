import { MessageSquarePlus } from 'lucide-react'
import { useNavigate } from 'react-router'
import { useCreateConversation } from '../../features/conversations/api'
import { describeProblem } from '../../lib/messages'
import { Alert } from '../ui/Alert'
import { Button } from '../ui/Button'
import { ConversationList } from './ConversationList'

/** New-chat button + the user's conversations. Everything below the list lives in AppShell. */
export function ConversationNav() {
  const create = useCreateConversation()
  const navigate = useNavigate()

  async function newChat() {
    try {
      const created = await create.mutateAsync()
      void navigate(`/c/${created.id}`)
    } catch {
      // surfaced below via create.error
    }
  }

  return (
    <>
      <Button
        variant="secondary"
        className="mt-3 justify-start"
        onClick={newChat}
        loading={create.isPending}
      >
        {!create.isPending && <MessageSquarePlus className="size-4" aria-hidden />}
        New chat
      </Button>
      {create.isError && (
        <Alert variant="error" className="mt-2">
          {describeProblem(create.error).message}
        </Alert>
      )}
      <nav aria-label="Conversation history" className="mt-3 min-h-0 flex-1 overflow-y-auto">
        <ConversationList />
      </nav>
    </>
  )
}
