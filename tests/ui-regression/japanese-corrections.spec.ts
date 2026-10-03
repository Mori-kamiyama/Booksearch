import { test, expect, type Page } from '@playwright/test'

const design = { id: 1, title: 'デザイン入門', authors: '著者', thumbnail: null }
const index = { version: 1, books: [design], topics: [
  { id: 'genre-design', kind: 'genre', label: 'デザイン・アート', aliases: ['デザイン', 'アート'], count: 1 },
], coverage: { books: 1, page_count: 1, level: 0 } }

async function mock(page: Page, catalog = index) {
  const calls: URL[] = []
  await page.route('**/search-index.json', route => route.fulfill({ json: catalog }))
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/books/semantic/status') return route.fulfill({ json: { available: false } })
    if (url.pathname === '/api/books/search') {
      calls.push(url)
      const corrected = url.searchParams.get('q')?.startsWith('デザイン')
      return route.fulfill({ json: { books: corrected ? [design] : [], total: corrected ? 1 : 0 } })
    }
    return route.fulfill({ json: { books: [] } })
  })
  return calls
}

test('Japanese mixed input corrects to the searchable genre alias and preserves filters', async ({ page }) => {
  const calls = await mock(page)
  await page.goto('/search?q=デザiン&genre=genre-design&max_pages=300')
  await expect(page.getByRole('status')).toContainText('次の検索結果を表示しています：デザイン')
  await expect(page.getByRole('button', { name: design.title, exact: true })).toBeVisible()
  expect(calls.map(url => url.searchParams.get('q'))).toEqual(['デザiン', 'デザイン'])
  expect(calls.every(url => url.searchParams.get('genre') === 'genre-design' && url.searchParams.get('max_pages') === '300')).toBe(true)
})

test('multiple Japanese terms correct one token while retaining the rest', async ({ page }) => {
  const calls = await mock(page)
  await page.goto('/search?q=デザiン%20入門&max_pages=300')
  await expect(page.getByRole('status')).toContainText('次の検索結果を表示しています：デザイン 入門')
  expect(calls.map(url => url.searchParams.get('q'))).toEqual(['デザiン 入門', 'デザイン 入門'])
})

test('equally close candidates require a choice before another search', async ({ page }) => {
  const catalog = { ...index, topics: [], books: [
    { ...design, title: 'デザイン' }, { ...design, id: 2, title: 'デザイス' },
  ] }
  const calls = await mock(page, catalog)
  await page.goto('/search?q=デザイソ&max_pages=300')
  await expect(page.getByText('もしかして…', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'デザイン', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'デザイス', exact: true })).toBeVisible()
  expect(calls.map(url => url.searchParams.get('q'))).toEqual(['デザイソ'])
  await page.getByRole('button', { name: 'デザイン', exact: true }).click()
  await expect(page.getByRole('button', { name: design.title, exact: true })).toBeVisible()
  expect(new URL(page.url()).searchParams.get('max_pages')).toBe('300')
})

test('short kana and kanji substitutions stay selectable at 320px', async ({ page }) => {
  const calls = await mock(page, { ...index, books: [...index.books, { ...design, id: 2, title: '化学入門' }] })
  await page.setViewportSize({ width: 320, height: 700 })
  for (const [query, suggestion] of [['デザン', 'デザイン'], ['科学入門', '化学入門']]) {
    await page.goto(`/search?q=${encodeURIComponent(query)}`)
    await expect(page.getByText('もしかして…', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: suggestion, exact: true })).toBeVisible()
    expect(calls.filter(url => url.searchParams.get('q') === suggestion)).toHaveLength(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  }
})
