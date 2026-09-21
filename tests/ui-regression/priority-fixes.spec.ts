import { test, expect, type Page, type Route } from '@playwright/test'

const book = { id: 1, title: '検証用の蔵書', authors: '検証著者', thumbnail: null, shelf_candidates: [] }
const candidate = {
  book_id: 1, title: book.title, shelf_id: 'base-01-c02-r04',
  confidence: 0.9, observations: 3, updated_at: '2026-09-21T00:00:00Z',
}
type Override = (route: Route, url: URL) => Promise<boolean>

async function mockApi(page: Page, override?: Override) {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) {
      // Keep the tests entirely local, including optional cover images.
      if (url.hostname !== '127.0.0.1' && url.hostname !== 'localhost') return route.abort()
      return route.continue()
    }
    if (override && await override(route, url)) return
    let data: unknown = {}
    if (url.pathname === '/api/books/search') data = { books: [{ ...book, title: url.searchParams.get('q') }] }
    else if (url.pathname === '/api/books/featured') data = { books: [] }
    else if (url.pathname === '/api/books/index') data = { books: [book] }
    else if (url.pathname === '/api/shelf-candidates') data = { candidates: [candidate] }
    else if (url.pathname.startsWith('/api/books/')) data = book
    else if (url.pathname.startsWith('/api/jobs/')) data = { job_id: url.pathname.split('/').pop(), status: 'done', catalog: { entries: [] } }
    await route.fulfill({ json: data })
  })
  return errors
}

test('new search stays visible when an earlier response arrives late', async ({ page }) => {
  await mockApi(page, async (route, url) => {
    if (url.pathname !== '/api/books/search' || url.searchParams.get('q') !== 'slow') return false
    await new Promise(resolve => setTimeout(resolve, 900))
    await route.fulfill({ json: { books: [{ ...book, title: 'slow' }] } })
    return true
  })
  await page.goto('/search?q=slow')
  await page.locator('input').fill('fast')
  await page.locator('input').press('Enter')
  await expect(page.getByRole('button', { name: 'fast', exact: true })).toBeVisible()
  // Let the deliberately delayed old response reach the component.
  await page.waitForTimeout(1100)
  await expect(page.getByRole('button', { name: 'fast', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'slow', exact: true })).toHaveCount(0)
})

for (const width of [320, 375, 768, 820, 1024]) {
  test(`search content fits ${width}px without clipping`, async ({ page }) => {
    await page.setViewportSize({ width, height: 812 })
    await mockApi(page, async (route, url) => {
      if (url.pathname !== '/api/books/search') return false
      await route.fulfill({ json: { books: Array.from({ length: 10 }, (_, i) => ({ ...book, id: i + 1, title: i === 0 ? 'layout' : `layout ${i}` })) } })
      return true
    })
    await page.goto('/search?q=layout')
    await expect(page.getByRole('button', { name: 'layout', exact: true })).toBeVisible()
    const rect = await page.locator('main form').boundingBox()
    expect(rect!.x).toBeGreaterThanOrEqual(0)
    expect(rect!.x + rect!.width).toBeLessThanOrEqual(width + 1)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1)
    const covers = await page.locator('main button.tap-card > div').evaluateAll(nodes => nodes.slice(0, 2).map(node => { const r = node.getBoundingClientRect(); return { left: r.left, right: r.right } }))
    expect(covers[0].right).toBeLessThanOrEqual(covers[1].left)
  })
}

test('initial job error is visible and can be retried', async ({ page }) => {
  let failed = true
  await mockApi(page, async (route, url) => {
    if (!url.pathname.startsWith('/api/jobs/') || !failed) return false
    await route.fulfill({ status: 404, json: { error: 'not found' } })
    return true
  })
  await page.goto('/jobs/retry')
  await expect(page.getByRole('button', { name: /再試行/ })).toBeVisible()
  failed = false
  await page.getByRole('button', { name: /再試行/ }).click()
  await expect(page.getByRole('heading', { name: '終了', exact: true })).toBeVisible()
})

for (const status of ['uploading', 'unexpected_status', 'no_detection', 'no_readable_crops']) {
  test(`job ${status} is never presented as successful completion`, async ({ page }) => {
    await mockApi(page, async (route, url) => {
      if (!url.pathname.startsWith('/api/jobs/')) return false
      await route.fulfill({ json: { job_id: url.pathname.split('/').pop(), status, catalog: { entries: [] } } })
      return true
    })
    await page.goto('/jobs/state')
    await expect(page.locator('main h1')).toBeVisible()
    await expect(page.getByText('結果を読み込んでいます…', { exact: true })).toHaveCount(0)
    await expect(page.getByRole('heading', { name: '終了', exact: true })).toHaveCount(0)
    await expect(page.getByText('スキャンありがとうございました！！')).toHaveCount(0)
    await expect(page.locator('main')).not.toBeEmpty()
  })
}

test('unassigned shelf is not counted as a detected shelf', async ({ page }) => {
  await mockApi(page, async (route, url) => {
    if (!url.pathname.startsWith('/api/jobs/')) return false
    await route.fulfill({ json: { job_id: url.pathname.split('/').pop(), status: 'done', catalog: { entries: [{ box_id: 'a', shelf_id: null, books: [{ title: book.title }] }] } } })
    return true
  })
  await page.goto('/jobs/unassigned')
  await expect(page.getByRole('heading', { name: '終了', exact: true })).toBeVisible()
  await expect(page.getByText('0棚検知', { exact: true })).toBeVisible()
  await expect(page.getByText('1棚検知', { exact: true })).toHaveCount(0)
})

for (const id of ['%25', 'not-a-shelf']) {
  test(`invalid shelf ${id} has recovery instead of a blank page`, async ({ page }) => {
    const errors = await mockApi(page)
    await page.goto(`/map/${id}`)
    await expect(page.getByRole('link', { name: /マップ/ })).toBeVisible()
    expect(errors).toEqual([])
    await expect(page.getByRole('link', { name: 'この棚をスキャンして更新' })).toHaveCount(0)
  })
}

test('valid shelf keeps its location and scan action', async ({ page }) => {
  await mockApi(page)
  await page.goto('/map/base-01-c02-r04')
  await expect(page.getByRole('link', { name: 'この棚をスキャンして更新' })).toHaveAttribute('href', '/scan?shelf=base-01-c02-r04')
  await expect(page.getByRole('button', { name: new RegExp(book.title) })).toBeVisible()
})

for (const failedView of ['list', 'map']) {
  test(`index keeps the healthy view when ${failedView} fails and retries independently`, async ({ page }) => {
    let failed = true
    await mockApi(page, async (route, url) => {
      const endpoint = failedView === 'list' ? '/api/books/index' : '/api/shelf-candidates'
      if (url.pathname !== endpoint || !failed) return false
      await route.fulfill({ status: 500, json: { error: 'temporary failure' } })
      return true
    })
    const healthyView = failedView === 'list' ? 'map' : 'list'
    await page.goto(`/index?view=${healthyView}`)
    if (healthyView === 'map') await expect(page.getByText('この棚の本', { exact: true })).toBeVisible()
    else await expect(page.getByRole('button', { name: book.title, exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: /再試行/ })).toHaveCount(0)
    await page.getByRole('button', { name: failedView === 'list' ? 'リスト' : 'Map', exact: true }).click()
    await expect(page.getByRole('button', { name: /再試行/ })).toBeVisible()
    failed = false
    await page.getByRole('button', { name: /再試行/ }).click()
    await expect(page.getByRole('button', { name: /再試行/ })).toHaveCount(0)
    await expect(page.getByRole('button', { name: book.title, exact: true })).toBeVisible()
  })
}

test('an old search failure cannot replace a newer success', async ({ page }) => {
  await mockApi(page, async (route, url) => {
    if (url.pathname !== '/api/books/search' || url.searchParams.get('q') !== 'old-error') return false
    await new Promise(resolve => setTimeout(resolve, 700))
    await route.fulfill({ status: 500, json: { error: 'late failure' } })
    return true
  })
  await page.goto('/search?q=old-error')
  await page.locator('input').fill('current')
  await page.locator('input').press('Enter')
  await expect(page.getByRole('button', { name: 'current', exact: true })).toBeVisible()
  await page.waitForTimeout(900)
  await expect(page.getByRole('button', { name: 'current', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /再試行/ })).toHaveCount(0)
})

test('returning to an empty query invalidates an in-flight search', async ({ page }) => {
  await mockApi(page, async (route, url) => {
    if (url.pathname !== '/api/books/search') return false
    await new Promise(resolve => setTimeout(resolve, 700))
    await route.fulfill({ json: { books: [{ ...book, title: 'late result' }] } })
    return true
  })
  await page.goto('/search')
  await page.locator('input').fill('pending')
  await page.locator('input').press('Enter')
  await expect(page).toHaveURL(/q=pending/)
  await page.goBack()
  await expect(page.locator('input')).toHaveValue('')
  await page.waitForTimeout(900)
  await expect(page.getByRole('button', { name: 'late result', exact: true })).toHaveCount(0)
  await expect(page.getByText('見つかりませんでした', { exact: true })).toBeVisible()
})

test('a previous job cannot overwrite the job selected through navigation', async ({ page }) => {
  await mockApi(page, async (route, url) => {
    if (!url.pathname.startsWith('/api/jobs/')) return false
    const id = url.pathname.split('/').pop()
    if (id === 'old-job') await new Promise(resolve => setTimeout(resolve, 700))
    await route.fulfill({ json: { job_id: id, status: 'done', catalog: { entries: [{ box_id: 'a', shelf_id: null, books: [{ title: id }] }] } } })
    return true
  })
  await page.goto('/jobs/old-job')
  await page.evaluate(() => {
    history.pushState({}, '', '/jobs/new-job')
    dispatchEvent(new PopStateEvent('popstate'))
  })
  await expect(page.getByText('new-job', { exact: true })).toBeVisible()
  await page.waitForTimeout(900)
  await expect(page.getByText('new-job', { exact: true })).toBeVisible()
  await expect(page.getByText('old-job', { exact: true })).toHaveCount(0)
})

test('an old shelf failure cannot hide the newly selected shelf', async ({ page }) => {
  let calls = 0
  await mockApi(page, async (route, url) => {
    if (url.pathname !== '/api/shelf-candidates') return false
    calls += 1
    if (calls === 1) {
      await new Promise(resolve => setTimeout(resolve, 700))
      await route.fulfill({ status: 500, json: { error: 'old shelf failure' } })
    } else {
      await route.fulfill({ json: { candidates: [{ ...candidate, shelf_id: 'base-04-c08-r04' }] } })
    }
    return true
  })
  const requested = page.waitForRequest(request => request.url().includes('/api/shelf-candidates'))
  await page.goto('/map/base-01-c02-r04')
  await requested
  await page.evaluate(() => {
    history.pushState({}, '', '/map/base-04-c08-r04')
    dispatchEvent(new PopStateEvent('popstate'))
  })
  await expect(page.getByRole('button', { name: new RegExp(book.title) })).toBeVisible()
  await page.waitForTimeout(900)
  await expect(page.getByRole('button', { name: new RegExp(book.title) })).toBeVisible()
  await expect(page.getByRole('button', { name: /再試行/ })).toHaveCount(0)
})

for (const view of ['map', 'list']) {
  test(`duplicate ${view} retries keep the latest result`, async ({ page }) => {
    let calls = 0
    await mockApi(page, async (route, url) => {
      const endpoint = view === 'map' ? '/api/shelf-candidates' : '/api/books/index'
      if (url.pathname !== endpoint) return false
      calls += 1
      if (calls <= 2) {
        if (calls === 2) await new Promise(resolve => setTimeout(resolve, 700))
        await route.fulfill({ status: 500, json: { error: 'retry failure' } })
      } else {
        await route.fulfill({ json: view === 'map' ? { candidates: [candidate] } : { books: [book] } })
      }
      return true
    })
    await page.goto(`/index?view=${view}`)
    const retry = page.getByRole('button', { name: /再試行/ })
    await expect(retry).toBeVisible()
    // Two click events before React commits the loading state model a rapid
    // double retry. The earlier retry fails after the newer one succeeds.
    await retry.evaluate((element: HTMLButtonElement) => { element.click(); element.click() })
    await expect(page.getByRole('button', { name: book.title, exact: true })).toBeVisible()
    await page.waitForTimeout(900)
    await expect(page.getByRole('button', { name: book.title, exact: true })).toBeVisible()
    await expect(retry).toHaveCount(0)
  })
}

// Batch 2: truthful scan-result navigation, file-mode recovery, and covers.
const testImage = {
  name: 'shelf.png', mimeType: 'image/png',
  buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64'),
}

test('scan results link only accepted catalog matches and offer next actions', async ({ page }) => {
  await mockApi(page, async (route, url) => {
    if (!url.pathname.startsWith('/api/jobs/')) return false
    const matched = (title: string, id: number, confidence?: string) => ({
      title: `OCR ${title}`, book_lookup: { candidates: [{ title, library_db_id: id, match_confidence: confidence }] },
    })
    await route.fulfill({ json: {
      job_id: 'links', status: 'done', catalog: { entries: [{ box_id: 'a', shelf_id: candidate.shelf_id, books: [
        matched('確かな本', 42, 'auto'), matched('候補の本', 43, 'review'),
        matched('確度なしの本', 44), matched('無効IDの本', 0, 'auto'), { title: '未照合の文字' },
      ] }] },
    } })
    return true
  })
  await page.goto('/jobs/links')
  await expect(page.getByRole('heading', { name: '終了', exact: true })).toBeVisible()
  await expect(page.locator('main a[href="/books/42"]')).toBeVisible()
  await expect(page.locator('main a[href^="/books/"]')).toHaveCount(1)
  await expect(page.getByText('候補の本', { exact: true })).toBeVisible()
  await expect(page.locator('main a[href="/"]')).toBeVisible()
  await expect(page.locator('main a[href="/scan"]')).toBeVisible()
  await page.locator('main a[href="/books/42"]').click()
  await expect(page).toHaveURL(/\/books\/42$/)
})

test('a later accepted scan match upgrades a repeated review candidate', async ({ page }) => {
  await mockApi(page, async (route, url) => {
    if (!url.pathname.startsWith('/api/jobs/')) return false
    await route.fulfill({ json: { job_id: 'upgrade', status: 'done', catalog: { entries: [
      { box_id: 'early', shelf_id: candidate.shelf_id, books: [{ title: 'OCR', book_lookup: { candidates: [{ title: '同じ本', library_db_id: 42, match_confidence: 'review' }] } }] },
      { box_id: 'later', shelf_id: candidate.shelf_id, books: [{ title: 'OCR', book_lookup: { candidates: [{ title: '同じ本', library_db_id: 42, match_confidence: 'auto' }] } }] },
    ] } } })
    return true
  })
  await page.goto('/jobs/upgrade')
  await expect(page.locator('main a[href="/books/42"]')).toHaveCount(1)
  await expect(page.getByText('同じ本', { exact: true })).toHaveCount(1)
})

test('file selection can return to camera without permission and select the same file again', async ({ page }) => {
  await mockApi(page)
  await page.addInitScript(() => {
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
      value: () => { (window as any).__cameraRequests = ((window as any).__cameraRequests ?? 0) + 1; return Promise.reject(new Error('unexpected camera request')) },
    })
  })
  await page.goto('/scan')
  const input = page.locator('input[type="file"]')
  await input.setInputFiles(testImage)
  await expect(page.getByRole('button', { name: '選択したファイルを解析', exact: true })).toBeVisible()
  await page.getByRole('button', { name: /カメラに戻る/ }).click()
  await expect(page.getByRole('button', { name: 'カメラを開始してライブスキャンを開始', exact: true })).toBeVisible()
  expect(await page.evaluate(() => (window as any).__cameraRequests ?? 0)).toBe(0)
  await expect(input).toHaveValue('')
  await input.setInputFiles(testImage)
  await expect(page.locator('p').filter({ hasText: /^shelf\.png$/ })).toBeVisible()
  await expect(page.getByRole('button', { name: '選択したファイルを解析', exact: true })).toBeEnabled()
})

test('file switching is disabled while an upload is being initialized', async ({ page }) => {
  let release!: () => void
  const hold = new Promise<void>(resolve => { release = resolve })
  await mockApi(page, async (route, url) => {
    if (url.pathname !== '/api/scan/init') return false
    await hold
    await route.fulfill({ status: 500, json: { error: 'controlled test failure' } })
    return true
  })
  await page.goto('/scan')
  await page.locator('input[type="file"]').setInputFiles(testImage)
  await page.getByRole('button', { name: '選択したファイルを解析', exact: true }).click()
  try {
    await expect(page.getByRole('button', { name: '選択したファイルを解析', exact: true })).toBeDisabled()
    await expect(page.getByRole('button', { name: /カメラに戻る/ })).toBeDisabled()
    await expect(page.getByRole('button', { name: '画像または動画を選択', exact: true })).toBeDisabled()
  } finally { release() }
  await expect(page.getByRole('button', { name: /カメラに戻る/ })).toBeEnabled()
  await expect(page.getByText(/アップロードに失敗しました/)).toBeVisible()
  await page.getByRole('button', { name: /カメラに戻る/ }).click()
  await expect(page.getByText(/アップロードに失敗しました/)).toHaveCount(0)
})

test('shelf detail preserves the cover returned with the shelf candidate', async ({ page }) => {
  const thumbnail = 'data:image/png;base64,' + testImage.buffer.toString('base64')
  await mockApi(page, async (route, url) => {
    if (url.pathname !== '/api/shelf-candidates') return false
    await route.fulfill({ json: { candidates: [{ ...candidate, thumbnail }] } })
    return true
  })
  await page.goto('/map/base-01-c02-r04')
  const card = page.getByRole('button', { name: new RegExp(book.title) })
  await expect(card.locator('img')).toHaveAttribute('src', thumbnail)
  await expect(card.getByText('No')).toHaveCount(0)
})

test('a title-only observation is not merged into a different identified copy', async ({ page }) => {
  await mockApi(page, async (route, url) => {
    if (!url.pathname.startsWith('/api/jobs/')) return false
    await route.fulfill({ json: { job_id: 'same-title', status: 'done', catalog: { entries: [
      { box_id: 'a', shelf_id: candidate.shelf_id, books: [
        { title: '同名の本' },
        { title: 'OCR', book_lookup: { candidates: [{ title: '同名の本', library_db_id: 42, match_confidence: 'auto' }] } },
      ] },
    ] } } })
    return true
  })
  await page.goto('/jobs/same-title')
  await expect(page.getByText('同名の本', { exact: true })).toHaveCount(2)
  await expect(page.locator('main a[href="/books/42"]')).toHaveCount(1)
})

test('home recommendation failure retries without fabricated books', async ({ page }) => {
  let attempts = 0
  await mockApi(page, async (route, url) => {
    if (url.pathname !== '/api/books/featured') return false
    await route.fulfill(++attempts === 1 ? { status: 500, json: {} } : { json: { books: [book] } }); return true
  })
  await page.goto('/')
  await expect(page.getByText('おすすめを読み込めませんでした。')).toBeVisible()
  await page.getByRole('button', { name: '再試行', exact: true }).click()
  await expect(page.getByRole('button', { name: book.title, exact: true })).toBeVisible()
})

test('short home viewport can reach scan', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 480 })
  await mockApi(page)
  await page.goto('/')
  const scan = page.getByRole('button', { name: '本棚をスキャン', exact: true })
  await scan.scrollIntoViewIfNeeded()
  await expect(scan).toBeInViewport()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375)
})

test('detail ignores old responses and unknown reading time', async ({ page }) => {
  await mockApi(page, async (route, url) => {
    if (url.pathname !== '/api/books/1') return false
    await new Promise(resolve => setTimeout(resolve, 700))
    await route.fulfill({ json: { ...book, title: '古い本' } }); return true
  })
  await page.goto('/books/1')
  await page.evaluate(() => { history.pushState(null, '', '/books/2'); dispatchEvent(new PopStateEvent('popstate')) })
  await expect(page.getByRole('heading', { name: book.title, exact: true })).toBeVisible()
  await page.waitForTimeout(900)
  await expect(page.getByRole('heading', { name: book.title, exact: true })).toBeVisible()
  await expect(page.getByText(/推定読了/)).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '本の紹介' })).toBeVisible()
})

test('shelf freshness uses latest observation', async ({ page }) => {
  await mockApi(page, async (route, url) => {
    if (url.pathname !== '/api/shelf-candidates') return false
    await route.fulfill({ json: { candidates: [
      { ...candidate, confidence: 0.99, updated_at: '2020-01-01T00:00:00Z' },
      { ...candidate, book_id: 2, title: '最近の本', confidence: 0.5, updated_at: new Date().toISOString() },
    ] } }); return true
  })
  await page.goto('/map/base-01-c02-r04')
  await expect(page.getByText('本の最新観測（棚全体の確認日ではありません）')).toBeVisible()
  await expect(page.getByText('今日観測').first()).toBeVisible()
})

test('large index renders a bounded batch', async ({ page }) => {
  await mockApi(page, async (route, url) => {
    if (url.pathname !== '/api/books/index') return false
    await route.fulfill({ json: { books: Array.from({ length: 500 }, (_, i) => ({ ...book, id: i + 1, title: `本${i}`, title_reading: 'ほん' })) } }); return true
  })
  await page.goto('/index?view=list')
  await expect(page.locator('main .tap-card')).toHaveCount(120)
  await page.getByRole('button', { name: /さらに表示/ }).click()
  await expect(page.locator('main .tap-card')).toHaveCount(240)
})

test('search shows total rather than claiming the capped rows are all matches', async ({ page }) => {
  await mockApi(page, async (route, url) => {
    if (url.pathname !== '/api/books/search') return false
    await route.fulfill({ json: { books: [book], total: 200 } }); return true
  })
  await page.goto('/search?q=本')
  await expect(page.getByText('全200件中 1件表示')).toBeVisible()
})

test('search IME enter does not submit and clear restores focus', async ({ page }) => {
  await mockApi(page)
  await page.goto('/')
  const input = page.getByRole('searchbox', { name: '本を検索' })
  await input.fill('日本')
  await input.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true })
  await expect(page).toHaveURL(/\/$/)
  await page.getByRole('button', { name: '検索語を消す' }).click()
  await expect(input).toBeFocused()
  await input.fill('日本')
  await page.getByRole('button', { name: '検索', exact: true }).click()
  await expect(page).toHaveURL(/\/search\?q=/)
})

test('unknown routes have recovery and menu escape restores focus', async ({ page }) => {
  await mockApi(page)
  await page.goto('/unknown-route')
  await expect(page.getByRole('link', { name: 'ホームへ戻る' })).toBeVisible()
  const menu = page.getByRole('button', { name: 'メニューを開く' })
  await menu.click()
  await page.keyboard.press('Escape')
  await expect(menu).toBeFocused()
})

async function mockCamera(page: Page) {
  await page.addInitScript(() => {
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', { configurable: true, value: async () => {
      const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 480
      const ctx = canvas.getContext('2d')!
      const draw = () => {
        for (let y = 0; y < 480; y += 32) for (let x = 0; x < 640; x += 32) {
          ctx.fillStyle = ((x + y) / 32) % 2 ? '#aaa' : '#222'; ctx.fillRect(x, y, 32, 32)
        }
      }
      draw(); setInterval(draw, 100)
      return canvas.captureStream(10)
    } })
  })
}

test('scan session start is single-flight and failed finalize can be retried', async ({ page }) => {
  await mockCamera(page)
  let starts = 0; let finishes = 0
  const errors = await mockApi(page, async (route, url) => {
    if (url.pathname === '/api/scan/sessions') {
      starts++
      await new Promise(resolve => setTimeout(resolve, 300))
      await route.fulfill({ json: { session_id: 'session-test', frame_upload_url_endpoint: '/api/frame-init' } }); return true
    }
    if (url.pathname === '/api/scan/sessions/session-test/complete') {
      finishes++
      await route.fulfill(finishes === 1 ? { status: 500, body: 'try again' } : { json: { job_id: 'completed' } }); return true
    }
    if (url.pathname === '/api/frame-init') { await route.fulfill({ json: { upload_url: '/api/frame-put', frame_key: 'frame' } }); return true }
    if (url.pathname === '/api/jobs/session-test') { await route.fulfill({ json: { job_id: 'session-test', status: 'running' } }); return true }
    if (url.pathname === '/api/tags/detect') { await route.fulfill({ json: { tags: [] } }); return true }
    return false
  })
  await page.goto('/scan')
  await page.getByRole('button', { name: 'カメラを開始してライブスキャンを開始' }).dblclick()
  await expect(page.getByRole('button', { name: 'スキャンを終了', exact: true })).toBeVisible()
  expect(starts).toBe(1)
  await page.getByRole('button', { name: 'スキャンを終了', exact: true }).click()
  await expect(page.getByRole('button', { name: 'スキャンを確定して再試行' })).toBeVisible()
  await page.getByRole('button', { name: 'スキャンを確定して再試行' }).click()
  await expect(page).toHaveURL(/\/jobs\/completed$/)
  expect(finishes).toBe(2)
  expect(errors).toEqual([])
})

test('direct job entry returns home instead of leaving the app', async ({ page }) => {
  await mockApi(page)
  await page.goto('/jobs/direct')
  await page.getByRole('button', { name: '戻る', exact: true }).click()
  await expect(page).toHaveURL(/\/$/)
})

test('broken search cover has a placeholder and still opens the book', async ({ page }) => {
  await mockApi(page, async (route, url) => {
    if (url.pathname !== '/api/books/search') return false
    await route.fulfill({ json: { books: [{ ...book, thumbnail: 'https://invalid.example/cover.jpg' }], total: 1 } }); return true
  })
  await page.goto('/search?q=本')
  await expect(page.locator('[aria-label="表紙なし"]')).toBeVisible()
  await page.getByRole('button', { name: book.title, exact: true }).click()
  await expect(page).toHaveURL(/\/books\/1\?q=/)
})

test('leaving during file initialization never uploads or navigates back', async ({ page }) => {
  let release!: () => void
  const hold = new Promise<void>(resolve => { release = resolve })
  let initialized = false; let starts = 0; let uploads = 0
  await mockApi(page, async (route, url) => {
    if (url.pathname === '/api/scan/init') {
      initialized = true; await hold
      await route.fulfill({ json: { job_id: 'abandoned', upload_url: '/api/file-put', content_type: 'image/png' } }); return true
    }
    if (url.pathname === '/api/file-put') { uploads++; await route.fulfill({ body: '' }); return true }
    if (url.pathname === '/api/scan/start') { starts++; await route.fulfill({ json: { job_id: 'abandoned' } }); return true }
    return false
  })
  await page.goto('/scan')
  await page.locator('input[type="file"]').setInputFiles(testImage)
  await page.getByRole('button', { name: '選択したファイルを解析' }).click()
  await expect.poll(() => initialized).toBe(true)
  await page.getByRole('button', { name: '戻る', exact: true }).click()
  await expect(page).toHaveURL(/\/$/)
  release()
  await page.waitForTimeout(500)
  expect(uploads).toBe(0); expect(starts).toBe(0)
  await expect(page).toHaveURL(/\/$/)
})

for (const failUpload of [false, true]) {
  test(failUpload ? 'failed frame blocks finalization and leaves cancel available' : 'stop waits for an outstanding canvas encode and its upload', async ({ page }) => {
    await mockCamera(page)
    if (!failUpload) await page.addInitScript(() => {
      const original = HTMLCanvasElement.prototype.toBlob
      HTMLCanvasElement.prototype.toBlob = function (callback, ...args) {
        if (this === document.querySelectorAll('main canvas')[3]) {
          ;(window as any).__encoding = true
          ;(window as any).__releaseEncode = () => original.call(this, callback, ...args)
        } else original.call(this, callback, ...args)
      }
    })
    let completes = 0; let failed = false; let commits = 0
    await mockApi(page, async (route, url) => {
      if (url.pathname === '/api/scan/sessions') {
        await route.fulfill({ json: { session_id: 'encoded', frame_upload_url_endpoint: '/api/frame-init' } }); return true
      }
      if (url.pathname === '/api/frame-init') {
        failed = failUpload
        await route.fulfill(failUpload ? { status: 500, body: 'upload init failed' } : { json: { upload_url: '/api/frame-put', frame_key: 'frame' } }); return true
      }
      if (url.pathname.endsWith('/commit-frame')) { commits++; await route.fulfill({ json: {} }); return true }
      if (url.pathname.endsWith('/complete')) { completes++; await route.fulfill({ json: { job_id: 'finished-encode' } }); return true }
      if (url.pathname === '/api/jobs/encoded') { await route.fulfill({ json: { job_id: 'encoded', status: 'collecting' } }); return true }
      if (url.pathname === '/api/tags/detect') { await route.fulfill({ json: { tags: [] } }); return true }
      return false
    })
    await page.goto('/scan')
    await page.getByRole('button', { name: 'カメラを開始してライブスキャンを開始' }).click()
    await expect(page.getByRole('button', { name: 'スキャンを終了', exact: true })).toBeVisible()
    if (failUpload) await expect.poll(() => failed, { timeout: 9000 }).toBe(true)
    else await expect.poll(() => page.evaluate(() => !!(window as any).__encoding), { timeout: 9000 }).toBe(true)
    await page.getByRole('button', { name: 'スキャンを終了', exact: true }).click()
    if (failUpload) {
      await expect(page.getByText('保存できなかったフレームがあります。キャンセルして撮り直してください。')).toBeVisible()
      expect(completes).toBe(0)
      await page.getByRole('button', { name: 'キャンセル', exact: true }).click()
      await expect(page.getByRole('button', { name: 'ライブスキャンを開始', exact: true })).toBeVisible()
    } else {
      await page.waitForTimeout(150)
      expect(completes).toBe(0)
      await page.evaluate(() => (window as any).__releaseEncode())
      await expect(page).toHaveURL(/\/jobs\/finished-encode$/)
      expect(commits).toBe(1); expect(completes).toBe(1)
    }
  })
}
