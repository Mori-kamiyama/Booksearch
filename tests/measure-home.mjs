import { chromium } from '@playwright/test'
import { writeFile } from 'node:fs/promises'
import assert from 'node:assert/strict'

const url = process.env.FRONTEND_URL
const count = Number(process.env.SAMPLES ?? 20)
if (!url) throw new Error('Set FRONTEND_URL explicitly.')
if (!Number.isSafeInteger(count) || count < 1 || count > 100) throw new Error('SAMPLES must be 1–100.')
const browser = await chromium.launch()
const samples = []
try {
  for (let i = 0; i < count; i++) {
    const context = await browser.newContext()
    try {
      const page = await context.newPage()
      const apiRequests = []
      const errors = []
      page.on('request', request => { if (request.url().includes('/api/')) apiRequests.push(request.url()) })
      page.on('pageerror', error => errors.push(error.message))
      const start = performance.now()
      await page.goto(url, { waitUntil: 'domcontentloaded' })
      await page.waitForFunction(() => {
        const images = [...document.querySelectorAll('#root button img[src^="data:image/"]')]
        return images.length === 5 && images.every(image => image.complete && image.naturalWidth > 0)
      })
      const coversVisibleMs = Math.round(performance.now() - start)
      assert.deepEqual(apiRequests, [], 'home must not fetch an API to render its recommendations')
      assert.deepEqual(errors, [], 'home must hydrate without errors')
      const timing = await page.evaluate(() => {
        const navigation = performance.getEntriesByType('navigation')[0]
        const snapshot = JSON.parse(document.getElementById('featured-bootstrap').textContent)
        return {
          documentMs: Math.round(navigation.responseEnd),
          connectionMs: Math.round(navigation.connectEnd - navigation.connectStart),
          documentBytes: navigation.transferSize,
          week: snapshot.week,
          books: snapshot.books.length,
        }
      })
      samples.push({ coversVisibleMs, ...timing })
    } finally { await context.close() }
  }
} finally { await browser.close() }
const sorted = samples.map(sample => sample.coversVisibleMs).sort((a, b) => a - b)
const result = {
  measuredAt: new Date().toISOString(), url, count,
  conditions: 'Serial desktop Chromium, fresh cache/storage; all five embedded covers decoded; no API warmup; no CPU/network throttling.',
  medianMs: sorted[Math.ceil(count * 0.5) - 1], p95Ms: sorted[Math.ceil(count * 0.95) - 1], samples,
}
if (process.env.OUTPUT) await writeFile(process.env.OUTPUT, JSON.stringify(result, null, 2) + '\n')
console.log(JSON.stringify(result, null, 2))
