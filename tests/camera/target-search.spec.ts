import { test, expect } from '@playwright/test'

test('search scan sends its target and hides background book results', async ({ page }, testInfo) => {
  if (process.env.MOBILE_VIEWPORT === '1') await page.setViewportSize({ width: 402, height: 874 })
  const targetShelfId = process.env.TARGET_SHELF_ID || 'base-01-c02-r04'
  const target = {
    id: 7, title: '探している本', authors: '著者', publisher: '', published_date: '',
    class_number: '', registration_number: '', isbn: '', thumbnail: null, info_link: null,
    shelf_candidates: [{ book_id: 7, shelf_id: targetShelfId, confidence: 0.8, observations: 1 }],
  }
  let sessionRequest: { target_book_id?: number; priority_shelf_id?: string } | null = null
  let uploads = 0
  await page.route('**/*', async route => {
    const request = route.request()
    const url = new URL(request.url())
    if (!url.pathname.startsWith('/api/')) {
      if (url.hostname !== '127.0.0.1' && url.hostname !== 'localhost') return route.abort()
      return route.continue()
    }
    const path = url.pathname
    if (path === '/api/books/7') return route.fulfill({ json: target })
    if (path === '/api/books/featured' || path.startsWith('/api/books/related')) return route.fulfill({ json: { books: [] } })
    if (path === '/api/scan/sessions') {
      sessionRequest = request.postDataJSON()
      return route.fulfill({ json: { session_id: 'target-camera', frame_upload_url_endpoint: '/api/camera-frame-init' } })
    }
    if (path === '/api/camera-frame-init') {
      return route.fulfill({ json: { upload_url: '/api/camera-frame-put', frame_key: request.postDataJSON().filename } })
    }
    if (path === '/api/camera-frame-put') {
      expect(request.postDataBuffer()?.subarray(0, 3).toString('hex')).toBe('ffd8ff')
      uploads++
      return route.fulfill({ json: {} })
    }
    if (path.endsWith('/commit-frame')) return route.fulfill({ json: {} })
    if (path.endsWith('/complete')) return route.fulfill({ json: { job_id: 'target-result' } })
    if (path === '/api/tags/detect') return route.fulfill({ json: { tags: [] } })
    if (path.startsWith('/api/jobs/')) return route.fulfill({ json: {
      job_id: path.split('/').pop(), status: path.endsWith('target-result') ? 'done' : 'collecting',
      catalog: { entries: [
        { box_id: 'other', shelf_id: 'different', books: [{ title: '関係のない本' }] },
        { box_id: 'target', shelf_id: targetShelfId, books: [{ title: target.title,
          book_lookup: { candidates: [{ title: target.title, library_db_id: 7, match_confidence: 'auto' }] } }] },
      ] },
    } })
    return route.fulfill({ json: {} })
  })

  await page.goto('/books/7')
  await page.getByRole('button', { name: 'スキャンして探す', exact: true }).click()
  await page.getByRole('button', { name: 'カメラを開始してライブスキャンを開始', exact: true }).click()
  await expect.poll(() => uploads, { timeout: 30_000 }).toBeGreaterThan(0)
  expect(sessionRequest).toEqual({ target_book_id: 7, priority_shelf_id: targetShelfId })
  await expect(page.getByTestId('scan-target-status')).toContainText('対象本を自動照合しました')
  await expect(page.getByText('関係のない本')).toHaveCount(0)
  if (process.env.EXPECT_TARGET_OVERLAY === '1') {
    await expect(page.getByTestId('target-shelf-overlay')).toBeVisible()
    if (process.env.EXPECT_TARGET_BOX === '1') {
      await expect(page.getByTestId('target-shelf-overlay')).toHaveAttribute('data-guide-mode', 'box')
    } else {
      await expect(page.getByTestId('target-shelf-overlay')).toHaveAttribute('aria-label', /左上方向/)
    }
    const plate = page.getByTestId('target-shelf-plate')
    await expect(plate).toBeVisible()
    const square = await plate.evaluate(element => {
      const rect = element.getBoundingClientRect()
      return { width: rect.width, height: rect.height, animation: getComputedStyle(element).animationName }
    })
    if (process.env.EXPECT_TARGET_BOX === '1') {
      expect(square.width).toBeGreaterThan(180)
      expect(square.height).toBeGreaterThan(180)
    } else expect(square.width).toBe(square.height)
    expect(square.animation).toBe('scan-target-plate-pulse')
    await page.screenshot({ path: testInfo.outputPath('target-overlay.png') })
  }
})
