import { createContext, useContext } from 'react'

export type ToastInput = {
  message: string
  /** A link shown next to the message ("Manage"). */
  action?: { label: string; to: string }
}

/** How long a toast stays before it goes away on its own. */
export const TOAST_MS = 8000

export type Toasts = { show: (toast: ToastInput) => void }

// Outside a provider (a component test, say) showing a toast is a no-op, so a hook that may toast
// can be used anywhere.
export const ToastContext = createContext<Toasts>({ show: () => undefined })

export function useToasts(): Toasts {
  return useContext(ToastContext)
}
