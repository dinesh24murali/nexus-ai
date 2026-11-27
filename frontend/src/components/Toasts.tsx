import { X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { ToastContext, TOAST_MS, type ToastInput } from './toast-context'

type Toast = ToastInput & { id: number }

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: (id: number) => void }) {
  useEffect(() => {
    const timer = window.setTimeout(() => onDismiss(toast.id), TOAST_MS)
    return () => clearTimeout(timer)
  }, [toast.id, onDismiss])

  return (
    <li className="pointer-events-auto flex items-center gap-3 rounded-lg border border-border bg-surface px-4 py-3 text-body-sm shadow-lg">
      <span aria-hidden className="size-2 shrink-0 rounded-full bg-accent" />
      <span>{toast.message}</span>
      {toast.action && (
        <Link
          to={toast.action.to}
          onClick={() => onDismiss(toast.id)}
          className="font-medium text-primary underline underline-offset-2"
        >
          {toast.action.label}
        </Link>
      )}
      <button
        type="button"
        aria-label="Dismiss"
        onClick={() => onDismiss(toast.id)}
        className="-mr-1 rounded-sm p-1 text-fg-muted hover:text-fg"
      >
        <X className="size-4" aria-hidden />
      </button>
    </li>
  )
}

/** Small, quiet notices in the corner (functional spec: "Memory updated"). The region is announced politely and is not a `status` role, so pages keep exactly one of those (the running tool call). */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const next = useRef(0)
  const dismiss = useCallback(
    (id: number) => setToasts((all) => all.filter((t) => t.id !== id)),
    [],
  )
  const show = useCallback((toast: ToastInput) => {
    setToasts((all) => [...all, { ...toast, id: ++next.current }])
  }, [])
  const value = useMemo(() => ({ show }), [show])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <section
        aria-live="polite"
        aria-label="Notifications"
        className="pointer-events-none fixed right-4 bottom-4 z-50"
      >
        {toasts.length > 0 && (
          <ul className="flex flex-col gap-2">
            {toasts.map((t) => (
              <ToastItem key={t.id} toast={t} onDismiss={dismiss} />
            ))}
          </ul>
        )}
      </section>
    </ToastContext.Provider>
  )
}
