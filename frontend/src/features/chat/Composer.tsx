import { ArrowUp, Square } from 'lucide-react'
import {
  useEffect,
  useRef,
  type Dispatch,
  type FormEvent,
  type KeyboardEvent,
  type SetStateAction,
} from 'react'
import { Button } from '../../components/ui/Button'

export const MAX_MESSAGE_CHARS = 16_000

type Props = {
  /** The draft lives in the page, so a refused hand-off from the home page can put it back. */
  text: string
  setText: Dispatch<SetStateAction<string>>
  /** Resolves true when the message was accepted; false keeps the text in the box. */
  onSend: (text: string) => Promise<boolean>
  onStop: () => void
  running: boolean
  stopping: boolean
  autoFocus?: boolean
}

export function Composer({
  text,
  setText,
  onSend,
  onStop,
  running,
  stopping,
  autoFocus = true,
}: Props) {
  const box = useRef<HTMLTextAreaElement>(null)
  const empty = text.trim() === ''

  useEffect(() => {
    if (autoFocus && !running) box.current?.focus()
  }, [autoFocus, running])

  async function submit() {
    if (running || empty) return
    const sent = text
    setText('') // feels instant; put it back if the server refuses
    const ok = await onSend(sent)
    if (!ok) setText((current) => (current === '' ? sent : current))
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    void submit()
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter adds a line. Not while an IME is composing (it uses Enter to commit).
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      void submit()
    }
  }

  return (
    <form
      onSubmit={onSubmit}
      className="flex items-end gap-2 rounded-lg border border-border bg-surface p-2 focus-within:border-primary focus-within:shadow-focus"
    >
      <label htmlFor="composer" className="sr-only">
        Message
      </label>
      <textarea
        id="composer"
        ref={box}
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
        rows={1}
        maxLength={MAX_MESSAGE_CHARS}
        placeholder="Message Nexus…"
        className="max-h-48 min-h-10 flex-1 resize-none bg-transparent px-2 py-2 text-body outline-none focus-visible:shadow-none field-sizing-content placeholder:text-fg-muted"
      />
      {running ? (
        <Button type="button" variant="secondary" onClick={onStop} disabled={stopping}>
          <Square className="size-4" aria-hidden />
          Stop
        </Button>
      ) : (
        <Button type="submit" disabled={empty} aria-label="Send">
          <ArrowUp className="size-4" aria-hidden />
        </Button>
      )}
    </form>
  )
}
