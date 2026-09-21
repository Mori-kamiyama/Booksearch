import { test, expect } from '@playwright/test'

test('empty search can reduce terms and compare the recovered book', async ({ page }) => {
  await page.route('**/api/**', route => {
    const query = new URL(route.request().url()).searchParams.get('q')
    return route.fulfill({ json: { books: query === 'Python' ? [{ id: 1, title: 'Python入門', authors: '検索著者', published_date: '2024-01', class_number: '007', thumbnail: null, shelf_candidates: [] }] : [], total: query === 'Python' ? 1 : 0 } })
  })
  await page.goto('/search?q=Python%20入門書')
  await page.getByRole('button', { name: '検索条件を見直す' }).click()
  await expect(page.getByRole('searchbox')).toBeFocused()
  await page.getByRole('button', { name: '「Python」で検索' }).click()
  await expect(page).toHaveURL(/q=Python$/)
  const result = page.getByRole('button', { name: 'Python入門', exact: true })
  await expect(result).toBeVisible()
  await expect(result).toContainText('検索著者')
  await expect(result).toContainText('2024 / 分類 007')
  await expect(result).toContainText('棚の位置情報なし')
})
