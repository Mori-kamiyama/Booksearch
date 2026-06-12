import { test, expect } from '@playwright/test'

const API_BASE = process.env.API_BASE ?? 'https://rx7ylpbzg6.execute-api.ap-northeast-1.amazonaws.com'

test.describe('ホンノキ Frontend (CloudFront)', () => {
  test('ホーム表示とナビゲーション', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('text=ホンノキ')).toBeVisible()
    await expect(page.getByRole('link', { name: '本を探す' })).toBeVisible()
    await expect(page.getByRole('link', { name: '棚をスキャン' })).toBeVisible()
    await expect(page.getByRole('link', { name: '棚候補' })).toBeVisible()
    await expect(page.getByRole('heading', { name: '本を探す' })).toBeVisible()
  })

  test('本の検索: クエリ「python」で結果が表示される', async ({ page }) => {
    await page.goto('/')
    await page.fill('input[placeholder*="タイトル"]', 'python')
    await page.click('button:has-text("検索")')
    // 件数表示が出るまで待機
    await expect(page.locator('text=/\\d+ 件/')).toBeVisible({ timeout: 15000 })
    const cards = page.locator('p.font-bold.text-gray-800.text-base')
    await expect(cards.first()).toBeVisible()
    const count = await cards.count()
    expect(count).toBeGreaterThan(0)
  })

  test('検索: 存在しないクエリで「見つかりませんでした」表示', async ({ page }) => {
    await page.goto('/')
    await page.fill('input[placeholder*="タイトル"]', 'zzzz_no_such_book_xxx_qqq')
    await page.click('button:has-text("検索")')
    await expect(page.locator('text=見つかりませんでした')).toBeVisible({ timeout: 15000 })
  })

  test('棚候補ページが開く', async ({ page }) => {
    await page.goto('/shelves')
    await expect(page.locator('h2:has-text("棚候補")')).toBeVisible()
    // 候補がない初期状態 or 候補リスト
    const empty = page.locator('text=棚候補はまだありません')
    const list = page.locator('section').first()
    await expect(empty.or(list)).toBeVisible({ timeout: 10000 })
  })

  test('スキャンページが開きアップロード UI が表示される', async ({ page }) => {
    await page.goto('/scan')
    await expect(page.locator('h2:has-text("棚をスキャン")')).toBeVisible()
    await expect(page.locator('text=ファイルアップロード')).toBeVisible()
    await expect(page.locator('text=画像または動画を選択')).toBeVisible()
    await expect(page.locator('button:has-text("解析する")')).toBeDisabled()
  })

  test('SPA ルーティング: 直接 URL アクセスで 404 でも index.html', async ({ page }) => {
    const response = await page.goto('/jobs/non-existent-id')
    expect(response?.status()).toBe(200)
    await expect(page.locator('text=スキャン結果').or(page.locator('text=読み込み中'))).toBeVisible()
  })
})

test.describe('API 直接アクセス (E2E plumbing)', () => {
  test('GET /api/health', async ({ request }) => {
    const res = await request.get(`${API_BASE}/api/health`)
    expect(res.status()).toBe(200)
    const json = await res.json()
    expect(json.status).toBe('ok')
  })

  test('GET /api/books/search?q=python', async ({ request }) => {
    const res = await request.get(`${API_BASE}/api/books/search?q=python&limit=5`)
    expect(res.status()).toBe(200)
    const json = await res.json()
    expect(Array.isArray(json.books)).toBe(true)
    expect(json.books.length).toBeGreaterThan(0)
    expect(json.books[0]).toHaveProperty('title')
  })

  test('GET /api/shelf-candidates returns array', async ({ request }) => {
    const res = await request.get(`${API_BASE}/api/shelf-candidates`)
    expect(res.status()).toBe(200)
    const json = await res.json()
    expect(Array.isArray(json.candidates)).toBe(true)
  })

  test('POST /api/scan/init + PUT to S3 + /api/scan/start (presigned flow)', async ({ request }) => {
    const initRes = await request.post(`${API_BASE}/api/scan/init`, {
      data: { filename: 'e2e-test.png', content_type: 'image/png' },
    })
    expect(initRes.status()).toBe(200)
    const init = await initRes.json()
    expect(init.job_id).toBeTruthy()
    expect(init.upload_url).toMatch(/^https:\/\/.*\.s3\..*amazonaws\.com\//)

    // 1x1 PNG をデコードして PUT
    const tinyPng = Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=',
      'base64',
    )
    const putRes = await request.put(init.upload_url, {
      data: tinyPng,
      headers: { 'Content-Type': init.content_type },
    })
    expect(putRes.status()).toBe(200)

    const startRes = await request.post(`${API_BASE}/api/scan/start`, {
      data: { job_id: init.job_id },
    })
    expect(startRes.status()).toBe(202)

    const jobRes = await request.get(`${API_BASE}/api/jobs/${init.job_id}`)
    expect(jobRes.status()).toBe(200)
    const job = await jobRes.json()
    expect(job.job_id).toBe(init.job_id)
  })

  test('POST /api/scan (legacy base64)', async ({ request }) => {
    // 1x1 透明 PNG
    const tinyPng =
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='
    const res = await request.post(`${API_BASE}/api/scan`, {
      data: { filename: 'e2e-test.png', content_base64: tinyPng },
    })
    expect(res.status()).toBe(202)
    const json = await res.json()
    expect(json.job_id).toBeTruthy()

    // ジョブの初期 status を取得
    const jobRes = await request.get(`${API_BASE}/api/jobs/${json.job_id}`)
    expect(jobRes.status()).toBe(200)
    const job = await jobRes.json()
    expect(job.job_id).toBe(json.job_id)
    expect(['pending', 'running', 'failed', 'no_detection', 'no_readable_crops']).toContain(job.status)
  })
})
