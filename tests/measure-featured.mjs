import { chromium, request } from '@playwright/test'
import { writeFile } from 'node:fs/promises'

const frontendUrl = process.env.FRONTEND_URL
const apiBase = process.env.API_BASE
const count = Number(process.env.SAMPLES ?? 20)
if (!frontendUrl || !apiBase) throw new Error('Set FRONTEND_URL and API_BASE explicitly.')
if (!Number.isSafeInteger(count) || count < 1 || count > 100) throw new Error('SAMPLES must be 1–100.')

// Read-only. Warming the API is deliberate: this isolates fresh browser loading
// from server cold starts. Each sample has empty HTTP cache and local storage.
const api = await request.newContext({ baseURL: apiBase })
const browser = await chromium.launch()
const samples = []
try {
  const response = await api.get('/api/books/featured?limit=5')
  if (!response.ok()) throw new Error(`Featured API: ${response.status()}`)
  const { books } = await response.json()
  if (!books?.[0]?.title) throw new Error('No featured book to measure.')
  for (let i = 0; i < count; i++) {
    const context = await browser.newContext()
    try {
      const page = await context.newPage()
      const start = performance.now()
      const documentResponse = await page.goto(frontendUrl, { waitUntil: 'domcontentloaded' })
      await page.getByRole('button', { name: books[0].title, exact: true }).waitFor({ state: 'visible' })
      const visibleMs = Math.round(performance.now() - start)
      const timing = await page.evaluate(() => {
        const navigation = performance.getEntriesByType('navigation')[0]
        const resources = performance.getEntriesByType('resource')
        const featured = resources.find(entry => entry.name.includes('/api/books/featured?'))
        const scripts = resources.filter(entry => /\/assets\/.*\.js(?:\?|$)/.test(entry.name))
        return {
          documentMs: Math.round(navigation.responseEnd),
          dnsMs: Math.round(navigation.domainLookupEnd - navigation.domainLookupStart),
          connectionMs: Math.round(navigation.connectEnd - navigation.connectStart),
          documentResponseWaitMs: Math.round(navigation.responseStart - navigation.requestStart),
          domContentLoadedMs: Math.round(navigation.domContentLoadedEventEnd),
          featuredRequestMs: featured ? Math.round(featured.duration) : null,
          scriptTransferBytes: scripts.reduce((sum, entry) => sum + entry.transferSize, 0),
          scriptCount: scripts.length,
        }
      })
      samples.push({ visibleMs, ...timing, documentCache: await documentResponse?.headerValue('x-cache') })
    } finally {
      await context.close()
    }
  }
} finally {
  await browser.close()
  await api.dispose()
}
const sorted = samples.map(sample => sample.visibleMs).sort((a, b) => a - b)
const percentile = p => sorted[Math.ceil(sorted.length * p) - 1]
const report = {
  measuredAt: new Date().toISOString(), frontendUrl, count,
  conditions: 'Serial desktop Chromium; fresh browser contexts; API warmed; first recommendation text visible; no CPU/network throttling; covers not awaited.',
  medianMs: percentile(0.5), p95Ms: percentile(0.95), samples,
}
if (process.env.OUTPUT) await writeFile(process.env.OUTPUT, JSON.stringify(report, null, 2) + '\n')
console.log(JSON.stringify(report, null, 2))
