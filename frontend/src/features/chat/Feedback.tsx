import { ThumbsDown, ThumbsUp } from 'lucide-react'
import { cn } from '../../lib/cn'
import { describeProblem } from '../../lib/messages'
import type { Message } from './api'
import { useRate, type Rating } from './feedback'

const buttonClass =
  'inline-flex size-8 items-center justify-center rounded-md text-fg-muted transition-colors hover:bg-neutral-200/60 hover:text-fg disabled:cursor-not-allowed disabled:opacity-60 dark:hover:bg-neutral-800'

type Props = { message: Message }

/** Thumbs up/down under an answer. Pressing the other thumb changes the rating; the API has no
 *  way to withdraw one, so pressing the chosen thumb again does nothing. */
export function Feedback({ message }: Props) {
  const rate = useRate(message.conversation_id)
  const current = message.feedback ?? null

  function choose(rating: Rating) {
    if (current === rating || rate.isPending) return
    rate.mutate({ message, rating })
  }

  const option = (rating: Rating, label: string, icon: typeof ThumbsUp) => {
    const Icon = icon
    const pressed = current === rating
    return (
      <button
        type="button"
        aria-label={label}
        aria-pressed={pressed}
        disabled={rate.isPending}
        onClick={() => choose(rating)}
        className={cn(buttonClass, pressed && 'bg-neutral-200/60 text-fg dark:bg-neutral-800')}
      >
        <Icon className="size-4" aria-hidden />
      </button>
    )
  }

  return (
    <div className="flex items-center gap-1" role="group" aria-label="Rate this answer">
      {option(1, 'Good answer', ThumbsUp)}
      {option(-1, 'Bad answer', ThumbsDown)}
      {rate.isError && (
        <p role="alert" className="ml-2 text-caption text-warning-fg">
          {describeProblem(rate.error).message}
        </p>
      )}
    </div>
  )
}
