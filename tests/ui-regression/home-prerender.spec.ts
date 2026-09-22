import { test, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const cover = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aB1cAAAAASUVORK5CYII='
const snapshot = {
  week: '2026-39',
  books: Array.from({ length: 5 }, (_, index) => ({
    id: index + 1, title: `事前生成の本 ${index + 1}`, authors: '', publisher: '',
    published_date: '', class_number: '', registration_number: '', isbn: '',
    thumbnail: cover, info_link: null,
  })),
}
let home: string
test.beforeAll(async () => {
  const { renderHome } = await import(pathToFileURL(path.resolve('../frontend/.home-render/entry-home.js')).href)
  const template = await readFile('../frontend/dist/index.html', 'utf8')
  home = template.replace('<div id="root"></div>', `<div id="root" data-prerendered="home">${renderHome(snapshot)}</div>`)
    .replace('</body>', `<script type="application/json" id="featured-bootstrap">${JSON.stringify(snapshot)}</script></body>`)
})

test('all five covers are present without JavaScript or API requests', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false })
  try {
    const page = await context.newPage()
    const apiCalls: string[] = []
    page.on('request', request => { if (request.url().includes('/api/')) apiCalls.push(request.url()) })
    await page.route('http://127.0.0.1:4179/', route => route.fulfill({ contentType: 'text/html', body: home }))
    await page.goto('http://127.0.0.1:4179/')
    for (const book of snapshot.books) await expect(page.getByRole('button', { name: book.title, exact: true })).toBeVisible()
    const images = page.locator('button img[src^="data:image/"]')
    await expect(images).toHaveCount(5)
    expect(await images.evaluateAll(nodes => nodes.every(node => (node as HTMLImageElement).complete && (node as HTMLImageElement).naturalWidth > 0))).toBe(true)
    await expect(page.locator('.animate-pulse')).toHaveCount(0)
    expect(apiCalls).toEqual([])
  } finally { await context.close() }
})

test('hydration preserves the covers and enables search without fetching recommendations', async ({ page }) => {
  const errors: string[] = []
  const featuredRequests: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
  page.on('request', request => { if (request.url().includes('/api/books/featured')) featuredRequests.push(request.url()) })
  await page.route('http://127.0.0.1:4179/', route => route.fulfill({ contentType: 'text/html', body: home }))
  await page.route('**/api/books/search?**', route => route.fulfill({ json: { books: [], total: 0 } }))
  await page.goto('/')
  await expect(page.getByRole('button', { name: snapshot.books[0].title, exact: true })).toBeVisible()
  await page.getByRole('searchbox', { name: '本を検索' }).fill('図書室')
  await page.getByRole('button', { name: '検索', exact: true }).click()
  await expect(page).toHaveURL(/\/search\?q=/)
  await expect(page.getByText('見つかりませんでした', { exact: true })).toBeVisible()
  await page.goBack()
  await expect(page.getByRole('button', { name: snapshot.books[0].title, exact: true })).toBeVisible()
  expect(featuredRequests).toEqual([])
  expect(errors).toEqual([])
})
