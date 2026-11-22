import { Route, Routes } from 'react-router'
import { FilesPage } from './features/files/FilesPage'
import { MemoryPage } from './features/settings/Memory'
import { AppShell } from './routes/AppShell'
import { ConversationPage } from './routes/ConversationPage'
import { HomePage } from './routes/HomePage'
import { LoginPage } from './routes/LoginPage'
import { NotFoundPage } from './routes/NotFoundPage'
import { RegisterPage } from './routes/RegisterPage'
import { PublicOnly, RequireAuth } from './routes/guards'

export default function App() {
  return (
    <Routes>
      <Route element={<PublicOnly />}>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
      </Route>
      <Route element={<RequireAuth />}>
        <Route element={<AppShell />}>
          <Route index element={<HomePage />} />
          <Route path="c/:conversationId" element={<ConversationPage />} />
          <Route path="files" element={<FilesPage />} />
          <Route path="settings/memory" element={<MemoryPage />} />
        </Route>
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  )
}
