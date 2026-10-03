import { test, expect } from '@playwright/test'

// Read-only production acceptance checks against the catalog's design books.
test('Japanese mixed input recovers design books and preserves the original query', async ({ page }) => {
  await page.goto('/search?q=デザiン')
  await expect(page.getByRole('status')).toContainText('次の検索結果を表示しています：デザイン')
  const cards = page.locator('main button[aria-labelledby^="search-title-"]')
  await expect(cards.first()).toBeVisible()
  const title = await cards.first().locator('[id^="search-title-"]').innerText()
  await cards.first().click()
  await expect(page).toHaveURL(/\/books\/\d+\?/)
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
  await page.goBack()
  await expect(page.getByRole('status')).toContainText('次の検索結果を表示しています：デザイン')
  await page.getByRole('button', { name: '元の検索語「デザiン」で検索' }).click()
  await expect(page).toHaveURL(/exact=1/)
  await expect(page.getByText('見つかりませんでした', { exact: true })).toBeVisible()
})

test('Japanese multi-term correction keeps UI and the genre filter', async ({ page }) => {
  await page.goto('/search?q=UI%20デザiン&genre=genre-design')
  await expect(page.getByRole('status')).toContainText('次の検索結果を表示しています：UI デザイン')
  await expect(page.locator('main button[aria-labelledby^="search-title-"]').first()).toBeVisible()
  expect(new URL(page.url()).searchParams.get('genre')).toBe('genre-design')
})

test('short Japanese typo offers a selectable suggestion', async ({ page }) => {
  await page.goto('/search?q=デザン')
  await expect(page.getByText('もしかして…', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'デザイン', exact: true }).click()
  await expect(page.locator('main button[aria-labelledby^="search-title-"]').first()).toBeVisible()
  expect(new URL(page.url()).searchParams.get('q')).toBe('デザイン')
})

test('unapproved semantic search stays disabled in production', async ({ request }) => {
  const api = process.env.API_BASE
  test.skip(!api, 'Set API_BASE for the semantic capability check')
  const response = await request.get(`${api}/api/books/semantic/status`)
  expect(response.ok()).toBeTruthy()
  expect(await response.json()).toEqual({ available: false })
})
