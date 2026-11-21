import type { ReactNode } from 'react'
import { ThemeToggle } from '../components/ThemeToggle'

/** Centered card used by /login and /register. */
export function AuthLayout({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className="relative flex min-h-screen items-center justify-center px-4 py-12">
      <ThemeToggle className="absolute right-4 top-4" />
      <div className="w-full max-w-sm">
        <p className="mb-6 text-center text-h4 text-primary">Nexus AI</p>
        <div className="rounded-lg border border-border bg-surface p-6 shadow-sm">
          <h1 className="mb-6 text-h3">{title}</h1>
          {children}
        </div>
      </div>
    </main>
  )
}
