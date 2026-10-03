import { test, expect } from '@playwright/test'

test.skip(process.env.SEMANTIC_ENABLED !== '1', 'Enable these checks for a release with a semantic index')

test('zero-result Japanese search shows semantic books and opens a detail', async ({ page }) => {
  await page.goto('/search?q=使いやすいアプリの画面を設計したい')
  const section = page.getByRole('region', { name: '意味の近い本' })
  const cards = section.locator('button[aria-labelledby^="search-title-"]')
  await expect(cards.first()).toBeVisible({ timeout: 20_000 })
  expect(await cards.count()).toBeLessThanOrEqual(5)
  const title = await cards.first().locator('[id^="search-title-"]').innerText()
  expect(title).toBe('UIデザインの教科書')
  await cards.first().click()
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
  expect(new URL(page.url()).searchParams.get('q')).toBe('使いやすいアプリの画面を設計したい')
})

test('manual semantic suggestions exclude visible keyword books', async ({ page }) => {
  await page.goto('/search?q=UI%20デザイン&genre=genre-design')
  await expect(page.locator('main [id^="search-title-"]').first()).toBeVisible()
  const keywordTitles = await page.locator('main [id^="search-title-"]').allTextContents()
  expect(keywordTitles.length).toBeGreaterThan(0)
  await page.getByRole('button', { name: '関連する本も探す', exact: true }).click()
  const section = page.getByRole('region', { name: '意味の近い本' })
  const cards = section.locator('button[aria-labelledby^="search-title-"]')
  await expect(cards.first()).toBeVisible({ timeout: 20_000 })
  expect(await cards.count()).toBeLessThanOrEqual(5)
  const titles = await section.locator('[id^="search-title-"]').allTextContents()
  expect(titles.some(title => keywordTitles.includes(title))).toBe(false)
})

test('semantic API respects exclusions, empty filters and exact queries', async ({ request }) => {
  const api = process.env.API_BASE
  test.skip(!api, 'Set API_BASE for API acceptance checks')
  const first = await request.get(`${api}/api/books/semantic`, { params: { q: 'ユーザビリティ', genre: 'genre-design' } })
  expect(first.ok()).toBeTruthy()
  const books = (await first.json()).books
  expect(books.length).toBeGreaterThan(0)
  expect(books.length).toBeLessThanOrEqual(5)
  const exclude = books.map((book: { id: number }) => book.id)
  const next = await request.get(`${api}/api/books/semantic`, { params: { q: 'ユーザビリティ', genre: 'genre-design', exclude: exclude.join(',') } })
  expect(next.ok()).toBeTruthy()
  expect((await next.json()).books.every((book: { id: number }) => !exclude.includes(book.id))).toBe(true)
  const empty = await request.get(`${api}/api/books/semantic`, { params: { q: 'ユーザビリティ', min_pages: '100000' } })
  expect(empty.ok()).toBeTruthy()
  expect((await empty.json()).books).toEqual([])
  const exact = await request.get(`${api}/api/books/semantic`, { params: { q: 'ユーザビリティ', exact: '1' } })
  expect(exact.status()).toBe(400)
})
