import { test, expect, type Page } from '@playwright/test'

const books = Array.from({ length: 65 }, (_, index) => ({
  id: index + 1,
  title: `alpha book ${index + 1}`,
  authors: '検索テスト著者',
  thumbnail: null,
  shelf_candidates: [],
}))

async function mockSearchApi(page: Page) {
  const requests: Array<{ query: string; offset: number }> = []
  await page.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) {
      if (url.hostname !== '127.0.0.1' && url.hostname !== 'localhost') return route.abort()
      return route.continue()
    }
    if (url.pathname === '/api/books/search') {
      const query = url.searchParams.get('q') ?? ''
      const offset = Number(url.searchParams.get('offset') ?? '0')
      requests.push({ query, offset })
      const result = query === 'alpha' ? books.slice(offset, offset + 30) : query === 'beta' ? [{ ...books[0], title: 'beta result' }] : []
      await route.fulfill({ json: { books: result, total: query === 'alpha' ? 65 : result.length } })
      return
    }
    if (url.pathname.startsWith('/api/books/') && !url.pathname.endsWith('/related')) {
      const id = Number(url.pathname.split('/').pop())
      await route.fulfill({ json: books[id - 1] ?? books[0] })
      return
    }
    if (url.pathname.endsWith('/related')) {
      await route.fulfill({ json: { books: [] } })
      return
    }
    await route.fulfill({ json: {} })
  })
  return requests
}

test('search pagination preserves query/page and restores scroll after detail back', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  const requests = await mockSearchApi(page)
  await page.goto('/search?q=alpha')

  await expect(page.getByRole('button', { name: 'alpha book 1', exact: true })).toBeVisible()
  await expect(page.getByText('全65件中 30件表示', { exact: true })).toBeVisible()
  const next = page.getByRole('button', { name: '次のページ', exact: true })
  await next.scrollIntoViewIfNeeded()
  await next.click()
  await expect(page).toHaveURL(/\/search\?q=alpha&page=2$/)
  await expect(page.getByRole('button', { name: 'alpha book 31', exact: true })).toBeVisible()
  expect(requests).toContainEqual({ query: 'alpha', offset: 30 })

  const savedScroll = await page.evaluate(() => {
    window.scrollTo(0, 600)
    return Math.round(window.scrollY)
  })
  const detailCard = page.getByRole('button', { name: 'alpha book 31', exact: true })
  await detailCard.dispatchEvent('pointerdown')
  await detailCard.evaluate(element => (element as HTMLElement).click())
  await expect(page).toHaveURL(/\/books\/31\?q=alpha&page=2$/)
  await expect(page.getByRole('heading', { name: 'alpha book 31', exact: true })).toBeVisible()
  await page.goBack()
  await expect(page).toHaveURL(/\/search\?q=alpha&page=2$/)
  await expect(page.getByRole('button', { name: 'alpha book 31', exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => Math.round(window.scrollY))).toBeGreaterThanOrEqual(Math.max(0, savedScroll - 2))

  const lastNext = page.getByRole('button', { name: '次のページ', exact: true })
  await lastNext.scrollIntoViewIfNeeded()
  await lastNext.click()
  await expect(page).toHaveURL(/\/search\?q=alpha&page=3$/)
  await expect(page.getByRole('button', { name: 'alpha book 61', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'alpha book 65', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '次のページ', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '前のページ', exact: true })).toBeEnabled()

  await page.locator('input[type="search"]').fill('beta')
  await page.locator('input[type="search"]').press('Enter')
  await expect(page).toHaveURL(/\/search\?q=beta$/)
  await expect(page.getByRole('button', { name: 'beta result', exact: true })).toBeVisible()
  expect(requests.at(-1)).toEqual({ query: 'beta', offset: 0 })
})

test('invalid and unsafe search pages fall back without an unsafe offset request', async ({ page }) => {
  const requests = await mockSearchApi(page)
  await page.goto('/search?q=alpha&page=not-a-page')
  await expect(page).toHaveURL(/\/search\?q=alpha$/)
  await expect(page.getByRole('button', { name: 'alpha book 1', exact: true })).toBeVisible()
  expect(requests.at(-1)).toEqual({ query: 'alpha', offset: 0 })

  await page.goto('/search?q=gamma&page=2')
  await expect(page).toHaveURL(/\/search\?q=gamma$/)
  await expect(page.getByText('見つかりませんでした', { exact: true })).toBeVisible()

  await page.goto('/search?q=alpha&page=0')
  await expect(page).toHaveURL(/\/search\?q=alpha$/)
  await expect(page.getByRole('button', { name: 'alpha book 1', exact: true })).toBeVisible()
  expect(requests.at(-1)).toEqual({ query: 'alpha', offset: 0 })

  await page.goto('/search?q=alpha&page=999999999999999999999999')
  await expect(page).toHaveURL(/\/search\?q=alpha$/)
  await expect(page.getByRole('button', { name: 'alpha book 1', exact: true })).toBeVisible()
  expect(requests.at(-1)).toEqual({ query: 'alpha', offset: 0 })
})
