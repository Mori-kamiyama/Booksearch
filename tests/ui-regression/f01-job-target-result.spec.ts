import { test, expect } from '@playwright/test'

const targetBook = { id: 7, title: '対象本を探す', shelfId: 'base-01-c02-r04' }

const cases = [
  {
    name: 'confirmed',
    status: 'done',
    entries: [{ box_id: 'a', shelf_id: 'base-01-c02-r04', books: [{ title: targetBook.title, book_lookup: { candidates: [{ title: targetBook.title, library_db_id: 7, match_confidence: 'auto' }] } }] }],
    label: '対象本を自動照合しました',
  },
  {
    name: 'candidate',
    status: 'done',
    entries: [{ box_id: 'a', shelf_id: 'base-01-c02-r04', books: [{ title: targetBook.title, book_lookup: { candidates: [{ title: targetBook.title, library_db_id: 8, match_confidence: 'auto' }] } }] }],
    label: '対象本の候補があります（要確認）',
  },
  {
    name: 'missing',
    status: 'done',
    entries: [],
    label: '対象本は未発見でした',
  },
  {
    name: 'processing',
    status: 'processing',
    entries: [],
    label: '対象本を探索中',
  },
] as const

for (const fixture of cases) {
  test(`target state is shown for ${fixture.name} job results`, async ({ page }) => {
    await page.route('**/*', async route => {
      const url = new URL(route.request().url())
      if (!url.pathname.startsWith('/api/')) {
        if (url.hostname !== '127.0.0.1' && url.hostname !== 'localhost') return route.abort()
        return route.continue()
      }
      if (url.pathname === `/api/jobs/${fixture.name}`) {
        await route.fulfill({ json: { job_id: fixture.name, status: fixture.status, catalog: { entries: fixture.entries } } })
        return
      }
      await route.fulfill({ json: { books: [] } })
    })

    await page.goto('/')
    await page.evaluate(({ name }) => {
      history.pushState({ usr: { targetBook: { id: 7, title: '対象本を探す', shelfId: 'base-01-c02-r04' }, returnTo: '/books/7?q=target&page=2' }, key: 'target' }, '', `/jobs/${name}`)
      dispatchEvent(new PopStateEvent('popstate'))
    }, { name: fixture.name })

    const status = page.getByTestId('target-result-status')
    await expect(status).toContainText(targetBook.title)
    await expect(status).toContainText(fixture.label)
    await expect(status.getByRole('link', { name: '対象本の詳細へ戻る', exact: true })).toHaveAttribute('href', `/books/${targetBook.id}?q=target&page=2`)
  })
}
