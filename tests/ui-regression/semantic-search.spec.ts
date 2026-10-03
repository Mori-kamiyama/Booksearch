import { test, expect, type Page } from '@playwright/test'

const literal = { id: 1, title: 'Python入門', authors: '著者', thumbnail: null }
const related = { id: 2, title: 'UI設計の教科書', authors: '関連著者', thumbnail: null }

async function setup(page: Page, keywordBooks = [] as typeof literal[], semanticFailure = false) {
  const calls: URL[] = []
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/books/semantic/status') return route.fulfill({ json: { available: true } })
    if (url.pathname === '/api/books/semantic') {
      calls.push(url)
      if (semanticFailure) return route.fulfill({ status: 503, json: { error: 'unavailable' } })
      return route.fulfill({ json: { books: [related] } })
    }
    if (url.pathname === '/api/books/search') return route.fulfill({ json: { books: keywordBooks, total: keywordBooks.length } })
    return route.fulfill({ json: { books: [] } })
  })
  return calls
}

test('zero results automatically load separate semantic candidates with hard filters', async ({ page }) => {
  const calls = await setup(page)
  await page.goto('/search?q=ユーザビリティ&genre=genre-it&topic=ui&max_pages=200&level=beginner')
  await expect(page.getByText('見つかりませんでした', { exact: true })).toBeVisible()
  const region = page.getByRole('region', { name: '意味の近い本' })
  await expect(region.getByRole('button', { name: related.title, exact: true })).toBeVisible()
  expect(calls).toHaveLength(1)
  for (const [key, value] of Object.entries({ q: 'ユーザビリティ', genre: 'genre-it', topic: 'ui', max_pages: '200', level: 'beginner' })) {
    expect(calls[0].searchParams.get(key)).toBe(value)
  }
  await page.setViewportSize({ width: 320, height: 700 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: 'test-results/semantic-search-mobile.png', fullPage: true })
  await region.getByRole('button', { name: related.title, exact: true }).click()
  await expect(page).toHaveURL(/\/books\/2\?/)
  expect(new URL(page.url()).searchParams.get('topic')).toBe('ui')
})

test('existing results require a click and stay visible after semantic search', async ({ page }) => {
  const calls = await setup(page, [literal])
  await page.goto('/search?q=Python')
  const button = page.getByRole('button', { name: '関連する本も探す' })
  await expect(button).toBeVisible()
  expect(calls).toHaveLength(0)
  await button.click()
  await expect(page.getByRole('button', { name: related.title, exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: literal.title, exact: true })).toBeVisible()
  expect(calls[0].searchParams.get('exclude')).toBe('1')
})

test('ISBN and explicit original-query searches never request semantic search', async ({ page }) => {
  const calls = await setup(page)
  let availabilityCalls = 0
  page.on('request', request => { if (request.url().includes('/semantic/status')) availabilityCalls++ })
  for (const url of ['/search?q=978-4-123456-78-9', '/search?q=ユーザビリティ&exact=1']) {
    await page.goto(url)
    await expect(page.getByText('見つかりませんでした', { exact: true })).toBeVisible()
    await expect(page.getByRole('region', { name: '意味の近い本' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '関連する本も探す' })).toHaveCount(0)
  }
  expect(calls).toHaveLength(0)
  expect(availabilityCalls).toBe(0)
})

test('semantic failures preserve ordinary search and offer retry', async ({ page }) => {
  const calls = await setup(page, [literal], true)
  await page.goto('/search?q=Python')
  await page.getByRole('button', { name: '関連する本も探す' }).click()
  await expect(page.getByText('関連する本を検索できませんでした。')).toBeVisible()
  await expect(page.getByRole('button', { name: literal.title, exact: true })).toBeVisible()
  await page.getByRole('button', { name: '関連する本を再検索' }).click()
  await expect.poll(() => calls.length).toBe(2)
})

test('late semantic responses cannot overwrite a new search', async ({ page }) => {
  await setup(page)
  let release!: () => void
  const pending = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/books/semantic?**', async route => {
    await pending
    await route.fulfill({ json: { books: [related] } }).catch(() => {})
  })
  await page.goto('/search?q=ユーザビリティ')
  await expect(page.getByText('意味の近い本を探しています…')).toBeVisible()
  await page.getByRole('combobox', { name: '本を検索' }).fill('9784123456789')
  await page.getByRole('combobox', { name: '本を検索' }).press('Enter')
  await expect(page).toHaveURL(/q=9784123456789/)
  release()
  await expect(page.getByText('見つかりませんでした', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: related.title, exact: true })).toHaveCount(0)
})

test('unconfigured servers keep the current search experience', async ({ page }) => {
  const calls = await setup(page, [literal])
  await page.route('**/api/books/semantic/status', route => route.fulfill({ json: { available: false } }))
  await page.goto('/search?q=Python')
  await expect(page.getByRole('button', { name: literal.title, exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '関連する本も探す' })).toHaveCount(0)
  expect(calls).toHaveLength(0)
})
