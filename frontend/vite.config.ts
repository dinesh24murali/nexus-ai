/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    // Never inline assets as data: URIs. The CSP is `default-src 'self'` (design system §3), which
    // blocks data: fonts; Vite would otherwise inline the smallest font subset.
    assetsInlineLimit: 0,
  },
  server: { proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } } },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    coverage: { provider: 'v8', include: ['src/**/*.{ts,tsx}'] },
  },
})
