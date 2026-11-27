import { cva, type VariantProps } from 'class-variance-authority'
import { Loader2 } from 'lucide-react'
import type { ButtonHTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

const button = cva(
  'inline-flex items-center justify-center gap-2 rounded-md font-medium text-label transition-colors disabled:cursor-not-allowed disabled:opacity-60',
  {
    variants: {
      variant: {
        primary: 'bg-primary text-on-primary hover:bg-primary-hover',
        secondary:
          'border border-border bg-surface text-fg hover:bg-neutral-100 dark:hover:bg-neutral-800',
        ghost: 'text-fg-muted hover:bg-neutral-200/60 hover:text-fg dark:hover:bg-neutral-800',
      },
      size: { md: 'h-10 px-4', sm: 'h-8 px-3' },
    },
    defaultVariants: { variant: 'primary', size: 'md' },
  },
)

type Props = ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof button> & { loading?: boolean }

export function Button({ variant, size, loading, className, children, disabled, ...rest }: Props) {
  return (
    <button
      className={cn(button({ variant, size }), className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading && <Loader2 className="size-4 animate-spin" aria-hidden />}
      {children}
    </button>
  )
}
