import { test, expect } from '@playwright/test'

// Opt-in live smoke checks. Deterministic UI/error/race tests are in ui-regression.
const API_BASE = process.env.API_BASE

test('home exposes a labelled search and scan action', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('searchbox', { name: '本を検索' })).toBeVisible()
  await expect(page.getByRole('button', { name: '検索', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '本棚をスキャン', exact: true })).toBeVisible()
})

test('search handles an absent title', async ({ page }) => {
  await page.goto('/search?q=zzzz_no_such_book_xxx_qqq')
  await expect(page.getByText('見つかりませんでした', { exact: true })).toBeVisible()
})

test('unknown route has a recovery link', async ({ page }) => {
  await page.goto('/non-existent-page')
  await expect(page.getByRole('link', { name: 'ホームへ戻る' })).toBeVisible()
})

for (const path of ['/api/health', '/api/books/search?q=python&limit=5', '/api/shelf-candidates']) {
  test(`read-only API smoke ${path}`, async ({ request }) => {
    test.skip(!API_BASE, 'Set API_BASE to run live API smoke checks')
    const response = await request.get(`${API_BASE}${path}`)
    expect(response.ok()).toBeTruthy()
    const json = await response.json()
    if (path.includes('/books/')) expect(Array.isArray(json.books)).toBeTruthy()
    if (path.includes('/shelf-candidates')) expect(Array.isArray(json.candidates)).toBeTruthy()
    if (path.includes('/health')) expect(json.status).toBe('ok')
  })
}
