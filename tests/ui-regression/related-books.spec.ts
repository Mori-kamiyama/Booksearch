import { test, expect } from '@playwright/test'

const book = (id: number, title: string) => ({ id, title, authors: '著者', publisher: '', published_date: '', class_number: '', registration_number: '', isbn: '', thumbnail: null, info_link: null, shelf_candidates: [] })

test('related books show stored reasons and never the source itself', async ({ page }) => {
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/books/1/related') return route.fulfill({ json: { books: [{ ...book(1, '対象本'), reasons: [] }, { ...book(2, '関連本'), reasons: ['同じ著者の作品'] }, { ...book(2, '重複'), reasons: [] }] } })
    if (path === '/api/books/1') return route.fulfill({ json: book(1, '対象本') })
    return route.fulfill({ json: { books: [], candidates: [] } })
  })
  await page.goto('/books/1')
  const related = page.getByRole('heading', { name: '関連する本' }).locator('..')
  await expect(related.getByRole('button')).toHaveCount(1)
  await expect(related.getByText('同じ著者の作品')).toBeVisible()
  await expect(related.getByText('関連本', { exact: true })).toBeVisible()
})

test('related failure leaves the book and scan action usable', async ({ page }) => {
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/related')) return route.fulfill({ status: 503, json: {} })
    if (path === '/api/books/1') return route.fulfill({ json: book(1, '対象本') })
    return route.fulfill({ json: { books: [], candidates: [] } })
  })
  await page.goto('/books/1')
  await expect(page.getByRole('heading', { name: '対象本', exact: true })).toBeVisible()
  await expect(page.getByText('関連する本を取得できませんでした。')).toBeVisible()
  await expect(page.getByRole('button', { name: 'スキャンしながら探す' })).toBeVisible()
})
