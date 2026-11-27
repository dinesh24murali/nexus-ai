import { ArrowUp, Square } from 'lucide-react'
import {
  useEffect,
  useId,
  useRef,
  useState,
  type ChangeEvent,
  type Dispatch,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
  type SetStateAction,
} from 'react'
import { Button } from '../../components/ui/Button'
import { matchCommands, optionId, type SlashCommand } from './modes'
import { SlashMenu } from './SlashMenu'

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
  /** Extra control at the start of the box (the paperclip). */
  attach?: ReactNode
}

export function Composer({
  text,
  setText,
  onSend,
  onStop,
  running,
  stopping,
  autoFocus = true,
  attach,
}: Props) {
  const box = useRef<HTMLTextAreaElement>(null)
  const menuId = useId()
  const empty = text.trim() === ''
  // The `/` menu: open while the draft is one `/word`, until Escape (which holds until the draft
  // changes). The highlighted row is clamped, so a shorter list never leaves it out of range.
  const [highlight, setHighlight] = useState(0)
  const [dismissed, setDismissed] = useState(false)
  const items = dismissed || running ? [] : matchCommands(text)
  const menuOpen = items.length > 0
  const active = Math.min(highlight, Math.max(items.length - 1, 0))

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

  function pick(command: SlashCommand) {
    setText(command.insert) // only text: the backend parses the command
    setDismissed(false)
    setHighlight(0)
    box.current?.focus()
  }

  function onChange(event: ChangeEvent<HTMLTextAreaElement>) {
    setText(event.target.value)
    setDismissed(false)
    setHighlight(0)
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    void submit()
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (menuOpen && !event.nativeEvent.isComposing) {
      const command = items[active]
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault()
        const step = event.key === 'ArrowDown' ? 1 : items.length - 1
        setHighlight((active + step) % items.length)
        return
      }
      if (event.key === 'Escape') {
        event.preventDefault()
        setDismissed(true)
        return
      }
      // Tab always completes. Enter completes too, unless the box already holds exactly that
      // command: then it is a message to send, not a menu to answer.
      const complete = event.key === 'Tab' || (event.key === 'Enter' && command.insert !== text)
      if (complete && !event.shiftKey) {
        event.preventDefault()
        pick(command)
        return
      }
    }
    // Enter sends, Shift+Enter adds a line. Not while an IME is composing (it uses Enter to commit).
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      void submit()
    }
  }

  return (
    <form
      onSubmit={onSubmit}
      className="relative flex items-end gap-2 rounded-lg border border-border bg-surface p-2 focus-within:border-primary focus-within:shadow-focus"
    >
      {menuOpen && <SlashMenu id={menuId} items={items} active={active} onPick={pick} />}
      {attach}
      <label htmlFor="composer" className="sr-only">
        Message
      </label>
      <textarea
        id="composer"
        ref={box}
        value={text}
        onChange={onChange}
        onKeyDown={onKeyDown}
        aria-autocomplete={menuOpen ? 'list' : undefined}
        aria-controls={menuOpen ? menuId : undefined}
        aria-activedescendant={menuOpen ? optionId(menuId, active) : undefined}
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
