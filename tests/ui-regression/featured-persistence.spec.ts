import { test, expect, type Page } from '@playwright/test'

const storedBook = {
  id: 501,
  title: '保存されたおすすめ',
  authors: '保存著者',
  thumbnail: null,
  shelf_candidates: [],
}

function seedFeatured(page: Page, savedAt: number, weekOverride?: string) {
  return page.addInitScript(({ book, savedAt: timestamp, weekOverride: seededWeek }) => {
    const date = new Date()
    const target = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()))
    const day = target.getUTCDay() || 7
    target.setUTCDate(target.getUTCDate() + 4 - day)
    const yearStart = new Date(Date.UTC(target.getUTCFullYear(), 0, 1))
    const week = Math.ceil(((target.getTime() - yearStart.getTime()) / 86_400_000 + 1) / 7)
    const weekKey = seededWeek ?? `${target.getUTCFullYear()}-${week}`
    const payload = JSON.stringify({ version: 1, week: weekKey, savedAt: timestamp, books: [book] })
    // The priority preview uses the production Vite env file; seed both the
    // preview origin and that configured API namespace so the test stays local.
    for (const environment of [location.origin, 'https://rx7ylpbzg6.execute-api.ap-northeast-1.amazonaws.com']) {
      localStorage.setItem(`booksearch:featured:v1:${encodeURIComponent(environment)}:5`, payload)
    }
  }, { book: storedBook, savedAt, weekOverride })
}

test('revisit displays persisted recommendations before a request', async ({ page }) => {
  let featuredRequests = 0
  await seedFeatured(page, Date.now())
  await page.route('**/api/books/featured*', async route => {
    featuredRequests += 1
    await route.abort()
  })

  const samples: number[] = []
  const startedAt = Date.now()
  await page.goto('/')
  await expect(page.getByRole('button', { name: storedBook.title, exact: true })).toBeVisible()
  samples.push(Date.now() - startedAt)
  for (let attempt = 0; attempt < 19; attempt += 1) {
    const reloadStartedAt = Date.now()
    await page.reload()
    await expect(page.getByRole('button', { name: storedBook.title, exact: true })).toBeVisible()
    samples.push(Date.now() - reloadStartedAt)
  }
  const sorted = [...samples].sort((a, b) => a - b)
  const p95 = sorted[Math.ceil(sorted.length * 0.95) - 1]
  console.log(`FEATURED_CACHE_MEASUREMENT samples=${samples.length} p95=${p95}ms environment=local-preview-not-production`)
  test.info().annotations.push({
    type: 'measurement',
    description: `simulated localStorage title display: ${samples.length} samples, p95=${p95}ms; this is not a production p95 measurement`,
  })
  expect(featuredRequests).toBe(0)
})

test('week-switched recommendations stay visible while a successful refresh replaces them', async ({ page }) => {
  await seedFeatured(page, Date.now(), '1900-1')
  await page.route('**/api/books/featured*', async route => {
    await new Promise(resolve => setTimeout(resolve, 250))
    await route.fulfill({ json: { books: [{ ...storedBook, title: '更新されたおすすめ' }] } })
  })

  const startedAt = Date.now()
  await page.goto('/')
  await expect(page.getByRole('button', { name: storedBook.title, exact: true })).toBeVisible({ timeout: 1_000 })
  const staleTitleMs = Date.now() - startedAt
  test.info().annotations.push({
    type: 'measurement',
    description: `simulated stale-cache title display: ${staleTitleMs}ms; this is not a production p95 measurement`,
  })
  await expect(page.getByRole('button', { name: '更新されたおすすめ', exact: true })).toBeVisible()
})

test('background refresh failure keeps the previous recommendations', async ({ page }) => {
  await seedFeatured(page, Date.now() - 6 * 60_000)
  await page.route('**/api/books/featured*', route => route.fulfill({ status: 503, json: { error: 'offline' } }))

  await page.goto('/')
  await expect(page.getByRole('button', { name: storedBook.title, exact: true })).toBeVisible()
  await expect(page.getByText('おすすめを読み込めませんでした。')).toHaveCount(0)
})

test('scan results disclose abandoned frames', async ({ page }) => {
  await page.route('**/api/jobs/partial-result', route => route.fulfill({ json: {
    job_id: 'partial-result', status: 'done', failed_frames: 2, catalog: { entries: [] },
  } }))
  await page.goto('/jobs/partial-result')
  await expect(page.getByText('一部の画像（2件）を解析できませんでした。結果に含まれていない本は、もう一度撮影してください。')).toBeVisible()
})
