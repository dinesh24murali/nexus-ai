import { defineConfig, devices } from '@playwright/test'

// Runs against a live stack: `make up` (LLM_MODE=fake for AI features), then `make verify`.
// Default URL is the Compose Caddy; override with APP_URL.
export default defineConfig({
  testDir: '.',
  testMatch: '**/*.spec.ts',
  fullyParallel: false, // tests share one database and the per-IP auth rate limits
  workers: 1,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: process.env.APP_URL ?? 'http://localhost:8080',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
