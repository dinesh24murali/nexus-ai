import { Eye, EyeOff } from 'lucide-react'
import { useId, useState, type InputHTMLAttributes, type ReactNode } from 'react'
import { cn } from '../../lib/cn'

type FieldProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> & {
  label: string
  /** Extra classes for the <input> itself (`className` styles the wrapper). */
  inputClassName?: string
  error?: string
  hint?: ReactNode
}

const inputClass = (invalid: boolean) =>
  cn(
    'h-10 w-full rounded-md border bg-surface px-3 text-body text-fg placeholder:text-fg-muted',
    'focus-visible:border-primary',
    invalid ? 'border-error-fg' : 'border-border',
  )

/** Label + input + error/hint, wired for screen readers (aria-invalid, aria-describedby). */
export function Field({
  label,
  error,
  hint,
  className,
  inputClassName,
  type,
  ...rest
}: FieldProps) {
  const id = useId()
  const describedBy = [error && `${id}-error`, hint && `${id}-hint`].filter(Boolean).join(' ')
  const [revealed, setRevealed] = useState(false)
  const isPassword = type === 'password'

  return (
    <div className={className}>
      <label htmlFor={id} className="mb-1 block text-label text-fg">
        {label}
      </label>
      <div className="relative">
        <input
          id={id}
          type={isPassword && revealed ? 'text' : type}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy || undefined}
          className={cn(inputClass(Boolean(error)), isPassword && 'pr-10', inputClassName)}
          {...rest}
        />
        {isPassword && (
          <button
            type="button"
            onClick={() => setRevealed((v) => !v)}
            aria-label={revealed ? 'Hide password' : 'Show password'}
            aria-pressed={revealed}
            className="absolute inset-y-0 right-0 flex w-10 items-center justify-center rounded-md text-fg-muted hover:text-fg"
          >
            {revealed ? (
              <EyeOff className="size-4" aria-hidden />
            ) : (
              <Eye className="size-4" aria-hidden />
            )}
          </button>
        )}
      </div>
      {hint && (
        <div id={`${id}-hint`} className="mt-1 text-caption text-fg-muted">
          {hint}
        </div>
      )}
      {error && (
        <p id={`${id}-error`} className="mt-1 text-caption text-error-fg">
          {error}
        </p>
      )}
    </div>
  )
}
