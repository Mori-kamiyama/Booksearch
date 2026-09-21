import { test, expect } from '@playwright/test'

const targetBook = {
  id: 7,
  title: '対象本を探す',
  authors: '検証著者',
  publisher: '検証出版社',
  published_date: '2026-01-01',
  class_number: '000',
  registration_number: '7',
  isbn: '',
  thumbnail: null,
  info_link: null,
  shelf_candidates: [{
    book_id: 7,
    title: '対象本を探す',
    shelf_id: 'base-01-c02-r04',
    confidence: 0.42,
    observations: 1,
  }],
}

test('book detail carries the target into live scan and returns to the detail', async ({ page }) => {
  await page.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) {
      if (url.hostname !== '127.0.0.1' && url.hostname !== 'localhost') return route.abort()
      return route.continue()
    }
    if (url.pathname === '/api/books/featured' || url.pathname.startsWith('/api/books/related')) {
      await route.fulfill({ json: { books: [] } })
      return
    }
    if (url.pathname === `/api/books/${targetBook.id}`) {
      await route.fulfill({ json: targetBook })
      return
    }
    await route.fulfill({ json: {} })
  })

  await page.goto(`/books/${targetBook.id}?q=${encodeURIComponent(targetBook.title)}`)
  await expect(page.getByRole('heading', { name: targetBook.title, exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'スキャンしながら探す', exact: true }).click()

  await expect(page).toHaveURL(/\/scan$/)
  await expect(page.getByTestId('scan-target-status')).toContainText(targetBook.title)
  await expect(page.getByTestId('scan-target-status')).toContainText('base-01-c02-r04')
  await expect(page.getByTestId('scan-target-status')).toContainText('対象本を探しています')

  await page.getByRole('button', { name: '戻る', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/books/${targetBook.id}\\?q=`))
  await expect(page.getByRole('heading', { name: targetBook.title, exact: true })).toBeVisible()
})
