import { defineConfig } from '@playwright/test'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

const input = process.env.CAMERA_VIDEO
if (!input || !existsSync(input) || !input.endsWith('.y4m')) {
  throw new Error('Set CAMERA_VIDEO to an existing Y4M video (see docs/virtual_camera_e2e.md).')
}
const live = process.env.CAMERA_LIVE === '1'
if (live && !process.env.FRONTEND_URL) throw new Error('Live camera tests require FRONTEND_URL; they create scan data.')
export default defineConfig({
  testDir: './camera',
  timeout: live ? 240_000 : 60_000,
  expect: { timeout: 20_000 },
  workers: 1,
  retries: 0,
  reporter: 'list',
  use: {
    browserName: 'chromium',
    channel: 'chromium',
    baseURL: live ? process.env.FRONTEND_URL : 'http://127.0.0.1:4182',
    permissions: ['camera'],
    launchOptions: { args: [
      '--use-fake-device-for-media-stream',
      `--use-file-for-fake-video-capture=${resolve(input)}`,
    ] },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: live ? undefined : {
    command: 'npm --prefix ../frontend run build && npm --prefix ../frontend run preview -- --host 127.0.0.1 --port 4182',
    url: 'http://127.0.0.1:4182',
    timeout: 120_000,
    reuseExistingServer: false,
  },
})
