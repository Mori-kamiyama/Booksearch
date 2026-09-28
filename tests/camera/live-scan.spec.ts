import { test, expect, type Response } from '@playwright/test'

const live = process.env.CAMERA_LIVE === '1'

for (const scenario of ['normal', 'finalize-retry', 'frame-retry', 'outage-recovery']) {
  const retryFinalize = scenario === 'finalize-retry'
  const retryFrames = scenario === 'frame-retry'
  test(`native video camera → frames → result (${scenario})`, async ({ page }, testInfo) => {
    test.skip(live && scenario !== 'normal', 'Failure injection is local only')
    const errors: string[] = []
    const apiResponses: { path: string; status: number }[] = []
    const frames: Buffer[] = []
    const committed: string[] = []
    let outage = scenario === 'outage-recovery'
    let completes = 0
    const failures = new Set<string>()
    page.on('pageerror', error => errors.push(error.message))
    if (!live) await page.route('**/*', async route => {
      const request = route.request()
      const url = new URL(request.url())
      if (!url.pathname.startsWith('/api/')) {
        if (url.hostname !== '127.0.0.1' && url.hostname !== 'localhost') return route.abort()
        return route.continue()
      }
      const path = url.pathname
      if (outage && path === '/api/camera-frame-init') return route.fulfill({ status: 503, body: 'Persistent outage' })
      if (retryFrames && ['/api/camera-frame-init', '/api/camera-frame-put', '/api/scan/sessions/camera-test/commit-frame'].includes(path) && !failures.has(path)) {
        failures.add(path)
        return route.fulfill({ status: 503, body: 'Transient frame failure' })
      }
      let data: unknown = {}
      if (path === '/api/scan/sessions') data = { session_id: 'camera-test', frame_upload_url_endpoint: '/api/camera-frame-init' }
      else if (path === '/api/camera-frame-init') {
        const { filename } = request.postDataJSON()
        data = { upload_url: '/api/camera-frame-put', frame_key: filename }
      } else if (path === '/api/camera-frame-put') {
        expect(request.method()).toBe('PUT')
        const jpeg = request.postDataBuffer()!
        expect(jpeg.subarray(0, 3).toString('hex')).toBe('ffd8ff')
        expect(jpeg.length).toBeGreaterThan(1000)
        frames.push(jpeg)
      } else if (path.endsWith('/commit-frame')) committed.push(request.postDataJSON().frame_key)
      else if (path.endsWith('/complete')) {
        expect(frames.length).toBeGreaterThan(0)
        expect(committed.length).toBe(frames.length)
        completes++
        if (retryFinalize && completes === 1) return route.fulfill({ status: 503, body: 'Injected finalize failure' })
        data = { job_id: 'camera-result' }
      } else if (path === '/api/tags/detect') data = { tags: [] }
      else if (path.startsWith('/api/jobs/')) data = {
        job_id: path.split('/').pop(), status: path.endsWith('camera-result') ? 'done' : 'running', catalog: { entries: [] },
      }
      else if (path === '/api/books/index' || path === '/api/books/featured') data = { books: [] }
      else if (path === '/api/shelf-candidates') data = { candidates: [] }
      else return route.fulfill({ status: 404, json: { error: `Unexpected test API: ${path}` } })
      await route.fulfill({ json: data })
    })
    let uploaded = 0
    let latestJobResponse: Response | undefined
    page.on('response', response => {
      if (response.request().method() === 'PUT' && response.ok()) uploaded++
      if (new URL(response.url()).pathname.startsWith('/api/')) apiResponses.push({ path: new URL(response.url()).pathname, status: response.status() })
      if (new URL(response.url()).pathname.startsWith('/api/jobs/') && response.ok()) latestJobResponse = response
    })
    await page.goto('/scan')
    await page.getByRole('button', { name: 'カメラを開始してライブスキャンを開始', exact: true }).click()
    await expect(page.getByRole('button', { name: 'スキャンを終了', exact: true })).toBeVisible()
    const camera = await page.locator('video').evaluate((video: HTMLVideoElement) => {
      const track = (video.srcObject as MediaStream).getVideoTracks()[0]
      return { width: video.videoWidth, height: video.videoHeight, state: track.readyState, label: track.label }
    })
    expect(camera.width).toBeGreaterThan(0)
    expect(camera.state).toBe('live')
    await testInfo.attach('camera.json', { body: JSON.stringify(camera), contentType: 'application/json' })
    if (outage) await expect(page.getByText(/候補フレームの保存に失敗しました/)).toBeVisible()
    else await expect.poll(() => uploaded, { timeout: 30_000 }).toBeGreaterThan(0)
    if (live) await page.waitForTimeout(10_000)
    await page.getByRole('button', { name: 'スキャンを終了', exact: true }).click()
    if (outage) {
      await expect(page.getByText('保存できなかったフレームがあります。再確定で送信を再試行できます。')).toBeVisible()
      expect(completes).toBe(0)
      outage = false
      await page.getByRole('button', { name: 'スキャンを確定して再試行' }).click()
    }
    if (retryFinalize) {
      await expect(page.getByRole('button', { name: 'スキャンを確定して再試行' })).toBeVisible()
      await page.getByRole('button', { name: 'スキャンを確定して再試行' }).click()
    }
    await expect(page).toHaveURL(/\/jobs\/[^/?]+/)
    await expect(page.getByRole('heading', { name: '終了', exact: true })).toBeVisible({ timeout: live ? 180_000 : 20_000 })
    if (live) {
      expect(latestJobResponse).toBeDefined()
      const job = await latestJobResponse!.json()
      await testInfo.attach('job.json', { body: JSON.stringify(job, null, 2), contentType: 'application/json' })
      expect(job.status).toBe('done')
      const titles = (job.catalog?.entries ?? []).flatMap((entry: { books?: { title?: string }[] }) =>
        (entry.books ?? []).map(book => book.title).filter(Boolean))
      expect(titles.length, 'The real scan must recognize at least one book, not just finish an empty job').toBeGreaterThan(0)
    }
    if (!live) {
      if (retryFrames) expect(failures.size).toBe(3)
      expect(completes).toBe(retryFinalize ? 2 : 1)
      expect(new Set(committed).size).toBe(committed.length)
      await testInfo.attach('first-upload.jpg', { body: frames[0], contentType: 'image/jpeg' })
    }
    await testInfo.attach('api-responses.json', { body: JSON.stringify(apiResponses), contentType: 'application/json' })
    await testInfo.attach('result-url.txt', { body: page.url(), contentType: 'text/plain' })
    expect(errors).toEqual([])
  })
}
