import { useQueryClient } from '@tanstack/react-query'
import { LogOut } from 'lucide-react'
import { Outlet, useNavigate } from 'react-router'
import { ConversationNav } from '../components/sidebar/ConversationNav'
import { ThemeToggle } from '../components/ThemeToggle'
import { Button } from '../components/ui/Button'
import { UsageMeter } from '../features/chat/UsageMeter'
import { useAuth } from '../lib/auth-context'

/** Signed-in layout: sidebar (conversations, account) + the routed page. */
export function AppShell() {
  const { user, manager } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  async function signOut() {
    await manager.logout()
    queryClient.clear() // nothing of this account may outlive the session in memory
    void navigate('/login', { replace: true })
  }

  return (
    <div className="flex h-screen">
      <aside
        aria-label="Sidebar"
        className="flex w-(--sidebar-width) shrink-0 flex-col border-r border-border bg-sidebar p-3"
      >
        <p className="px-2 py-1 text-h5 text-primary">Nexus AI</p>
        <ConversationNav />
        <UsageMeter />
        <div className="mt-3 flex items-center justify-between gap-2 border-t border-border pt-3">
          <p className="truncate px-2 text-body-sm" title={user?.email}>
            {user?.display_name}
          </p>
          <ThemeToggle />
        </div>
        <Button variant="ghost" size="sm" className="mt-2 justify-start" onClick={signOut}>
          <LogOut className="size-4" aria-hidden />
          Sign out
        </Button>
      </aside>
      <main className="min-w-0 flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
