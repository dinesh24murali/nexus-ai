import { AlertCircle, CheckCircle2, Info } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '../../lib/cn'

const styles = {
  error: { box: 'bg-error-bg text-error-fg', icon: AlertCircle, role: 'alert' as const },
  info: { box: 'bg-info-bg text-info-fg', icon: Info, role: 'status' as const },
  success: { box: 'bg-success-bg text-success-fg', icon: CheckCircle2, role: 'status' as const },
}

export function Alert({
  variant,
  children,
  className,
}: {
  variant: keyof typeof styles
  children: ReactNode
  className?: string
}) {
  const { box, icon: Icon, role } = styles[variant]
  return (
    <div
      role={role}
      className={cn('flex items-start gap-2 rounded-md p-3 text-body-sm', box, className)}
    >
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div>{children}</div>
    </div>
  )
}
