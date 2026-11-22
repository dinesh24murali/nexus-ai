import { cn } from '../../lib/cn'
import { optionId, type SlashCommand } from './modes'

type Props = {
  id: string
  items: readonly SlashCommand[]
  active: number
  onPick: (command: SlashCommand) => void
}

/**
 * The `/` menu above the composer. It only offers text to insert (the backend parses the command),
 * so it needs no knowledge of what a command does. Keyboard handling lives in the composer, whose
 * textarea keeps focus and points here with `aria-activedescendant`.
 */
export function SlashMenu({ id, items, active, onPick }: Props) {
  return (
    <ul
      id={id}
      role="listbox"
      aria-label="Commands"
      className="absolute bottom-full left-0 z-10 mb-2 w-full max-w-md overflow-hidden rounded-lg border border-border bg-surface py-1 shadow-lg"
    >
      {items.map((command, index) => (
        <li
          key={command.name}
          id={optionId(id, index)}
          role="option"
          aria-selected={index === active}
          // The textarea must keep focus: pressing an option must not blur it first.
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => onPick(command)}
          className={cn(
            'flex cursor-pointer items-baseline justify-between gap-4 px-3 py-2 text-body-sm',
            index === active && 'bg-neutral-100 dark:bg-neutral-800',
          )}
        >
          <span className="font-mono font-medium">{command.name}</span>
          <span className="truncate text-fg-muted">{command.hint}</span>
        </li>
      ))}
    </ul>
  )
}
