import '@fontsource-variable/inter'
import '@fontsource-variable/jetbrains-mono'
import './styles/theme.css'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import App from './App'
import { AuthManager } from './lib/auth'
import { AuthProvider } from './lib/AuthProvider'

const manager = new AuthManager({ fetch: (...args) => fetch(...args) })
const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 30_000 } } })

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        <AuthProvider manager={manager}>
          <App />
        </AuthProvider>
      </QueryClientProvider>
    </BrowserRouter>
  </StrictMode>,
)
