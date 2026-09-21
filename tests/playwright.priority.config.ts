import { defineConfig, devices } from '@playwright/test'

// Run against a local production preview. Every API response is intercepted by
// the spec; no production service or scan writes are used.
export default defineConfig({
  testDir: './ui-regression',
  testMatch: ['*.spec.ts'],
  timeout: 15_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  workers: 3,
  reporter: 'list',
  use: {
    baseURL: process.env.PRIORITY_FRONTEND_URL ?? 'http://127.0.0.1:4179',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: process.env.PRIORITY_FRONTEND_URL ? undefined : {
    command: 'npm --prefix ../frontend run build && npm --prefix ../frontend run preview -- --host 127.0.0.1 --port 4179',
    url: 'http://127.0.0.1:4179',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
